import json
import os
from uuid import uuid4
from typing import Dict, List, Optional
import re

import requests
import dateparser
from datetime import datetime, timedelta, time as dtime

from langchain.tools import tool
from langgraph.prebuilt import create_react_agent
from langgraph.checkpoint.memory import MemorySaver
from langchain_gigachat.chat_models import GigaChat

import sys
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

def _safe_text(s) -> str:
    if s is None:
        return ""
    if not isinstance(s, str):
        s = str(s)
    return s.encode("utf-8", "replace").decode("utf-8", "replace")

def _scrub(obj):
    if isinstance(obj, str):
        return _safe_text(obj)
    if isinstance(obj, list):
        return [_scrub(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _scrub(v) for k, v in obj.items()}
    return obj
def _safe_print(*parts):
    import sys
    txt = " ".join(_safe_text(p) for p in parts)
    sys.stdout.buffer.write((txt + "\n").encode("utf-8", "replace"))
    sys.stdout.buffer.flush()


class Agent:
    def __init__(self):
        self.history_length: Optional[int] = None
        self.system_prompt: Optional[str] = None
        self.authorization_key_: Optional[str] = None
        self.model_: Optional[str] = None
        self.url_request_: Optional[str] = None
        self.url_auth_: Optional[str] = None
        self.data_path_: Optional[str] = None
        self.is_corp: bool = False

        self.set_config("cfg.json")
        # self.get_giga_auth()
        self.create_agent()

    def set_config(self, path_to_config: str):
        with open(path_to_config, 'r', encoding='utf-8') as f:
            data = json.load(f)
            self.authorization_key_ = data['GigaChat_API_Key']
            self.url_request_ = data["url_request"]
            self.url_auth_ = data["url_auth"]
            self.model_ = data["GigaChat_model"]
            self.is_corp = data["is_corp"]
            self.history_length = data["history_length"]
            self.data_path_ = data["data_path"]
            sys_path = data.get("path_to_system_promt")
            if sys_path and os.path.exists(sys_path):
                with open(sys_path, 'r', encoding='utf-8') as sf:
                    self.system_prompt = sf.read()

    def create_agent(self):
        model = GigaChat(
            credentials=self.authorization_key_,
            scope="GIGACHAT_API_CORP" if self.is_corp else "GIGACHAT_API_PERS",
            model=self.model_,
            verify_ssl_certs=False
        )

        @tool("find_donation_url", return_direct=True)
        def find_donation_info(use_text: str):
            """
            Дает информацию, где пользователь может СДЕЛАТЬ ПОЖЕРТВОВАНИЕ онлайн.
            Вызывай, если в запросе явно есть намерение пожертвовать/донатить/перевести деньги/
            поддержать рублем/сделать взнос, например: "хочу пожертвовать", "куда можно задонатить",
            "перевести деньги на доброе дело", "поддержать фонда".
            """
            return "https://dobro.mail.ru"


        @tool("find_events_from_text", return_direct=True)
        def find_events_tool(user_text: str):
            """
            По тексту пользователя возвращает релевантные мероприятия,
            опираясь на время, место и дату.
            """

            model = LLM_Parser()
            parsed = model.generate(user_text)
            parsed = json.loads(parsed)
            print(f'PARSED: {parsed}')
            city, date, time_ = parsed["city"], parsed["date"], parsed["time_start"]

            results = self.search_events_from_json(
                city=city,
                date=date,
                time_start=time_,
                time_window_minutes=180,
                max_results=5,
            )
            return _scrub(results)

        tools = [find_events_tool, find_donation_info]

        # Агент
        self.agent_ = create_react_agent(
            model=model,
            tools=tools,
            state_modifier=self.system_prompt,
            checkpointer=MemorySaver()
        )

    def get_giga_auth(self):
        rquid = str(uuid4())
        payload = "scope=GIGACHAT_API_CORP" if self.is_corp else "scope=GIGACHAT_API_PERS"
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "RqUID": rquid,
            "Authorization": "Basic " + self.authorization_key_,
        }
        response = requests.post(self.url_auth_, headers=headers, data=payload, verify=False)
        response.raise_for_status()
        data = response.json()
        self.token_ = data["access_token"]
        self.token_expires_at = data["expires_at"]

    def send_request(self, messages: List[Dict]) -> str:
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.token_}",
        }
        data = {
            "model": self.model_,
            "profanity_check": True,
            "messages": messages,
        }
        resp = requests.post(self.url_request_, json=data, headers=headers, verify=False)
        resp.raise_for_status()
        result = resp.json()
        return result["choices"][0]["message"]["content"]

    @staticmethod
    def _parse_hhmm(s: Optional[str] = None) -> Optional[dtime]:
        if not s:
            return None
        return datetime.strptime(s.strip(), "%H:%M").time()

    @staticmethod
    def _within_interval(user_start: datetime, user_end: datetime,
                         ev_start: datetime, ev_end: datetime) -> bool:
        # Пересечение интервалов (касание краями НЕ считается)
        return not (user_end <= ev_start or ev_end <= user_start)

    @staticmethod
    def _city_matches(user_city: Optional[str], ev_city: Optional[str],
                      address: str, title: str, description: str) -> bool:
        if not user_city:
            return True
        uc = user_city.strip().lower()
        fields = [
            (ev_city or "").lower(),
            (address or "").lower(),
            (title or "").lower(),
            (description or "").lower(),
        ]
        return any(uc in f for f in fields if f)

    def search_events_from_json(
        self,
        *,
        city = None,
        date = None,
        time_start = None,
        time_window_minutes = 180,
        max_results = None
    ):
        """Ищет события в self.data_path_ по городу/дате/времени. Возвращает [{title, url, content}, ...]."""
        if not date:
            return []

        with open(self.data_path_, "r", encoding="utf-8") as f:
            dataset = json.load(f)
            # print(dataset) 

        user_date = datetime.strptime(date, "%Y-%m-%d").date()
        u_time = datetime.strptime(time_start, "%H:%M").time() if time_start else dtime(12, 0)
        user_start = datetime.combine(user_date, u_time) - timedelta(minutes=time_window_minutes)
        user_end = datetime.combine(user_date, u_time) + timedelta(minutes=time_window_minutes)

        results = []
        for ev in dataset:
            sch = ev.get("schedule") or {}
            loc = ev.get("location") or {}
            org = ev.get("organizer") or {}

            ev_date_str = sch.get("date")
            if not ev_date_str:
                continue
            try:
                ev_date = datetime.strptime(ev_date_str, "%Y-%m-%d").date()
            except ValueError:
                continue
            if ev_date != user_date:
                continue

            ev_ts = Agent._parse_hhmm(sch.get("time_start")) or dtime(0, 0)
            ev_te = Agent._parse_hhmm(sch.get("time_end")) or dtime(23, 59)
            ev_start = datetime.combine(ev_date, ev_ts)
            ev_end = datetime.combine(ev_date, ev_te)

            if not Agent._within_interval(user_start, user_end, ev_start, ev_end):
                continue

            if not Agent._city_matches(
                city,
                loc.get("city"),
                loc.get("address_full") or "",
                ev.get("title") or "",
                ev.get("description") or "",
            ):
                continue

            date_line = f"{ev_date.strftime('%Y-%m-%d')} {ev_ts.strftime('%H:%M')}-{ev_te.strftime('%H:%M')}"
            address = loc.get("address_full") or "Адрес не указан"
            org_name = org.get("name") or "Организатор не указан"
            content = f"{date_line} • {address} • {org_name}"

            results.append({
                "title": _safe_text(ev.get("title") or "Без названия"),
                "url": _safe_text(ev.get("url") or ""),
                "content": _safe_text(content)
            })

        results.sort(key=lambda r: r["content"])
        if max_results is not None:
            results = results[:max_results]

        lines = []
        for r in results:
            title = _safe_text(r.get("title", "Без названия"))
            content = _safe_text(r.get("content", ""))
            url = _safe_text(r.get("url", ""))
            if url:
                lines.append(f"• **{title}** — {content}\n  {url}")
            else:
                lines.append(f"• **{title}** — {content}")

        result_text = "Вот что нашёл:\n" + "\n".join(lines) if len(lines) > 0 else "К сожалению, таких мероприятий нет. Может поищем что-нибудь другое?"
        # print(result_text)
        return _safe_text(result_text)


class LLM_Parser:
    
    def __init__(self):
        self.system_prompt = None
        self.authorization_key_ = None
        self.model_ = None
        self.url_request_ = None
        self.url_auth_ = None
        
        self.set_config("cfg_parser.json")
        self.get_giga_auth()

    def set_config(self, path_to_config:str):
        with open(path_to_config, 'r') as f:
            data = json.load(f)
            self.authorization_key_ = data['GigaChat_API_Key']
            self.url_request_ = data["url_request"]
            self.url_auth_ = data["url_auth"]
            self.model_ = data["GigaChat_model"]
            self.is_corp = data["is_corp"]
            if os.path.exists(data["path_to_system_promt"]):
                with open(data["path_to_system_promt"], 'r', encoding='utf-8') as f:
                    self.system_prompt = f.read()

    def get_giga_auth(self):
        rquid = str(uuid4())
        payload = "scope=GIGACHAT_API_CORP" if self.is_corp else "scope=GIGACHAT_API_PERS"
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "RqUID": rquid,
            'Authorization': 'Basic '+self.authorization_key_
        }

        response = requests.request(
            "POST",
            self.url_auth_,
            headers=headers,
            data=payload,
            verify=False,
        )

        response = json.loads(response.text)
        self.token_ = response['access_token']
        self.token_expires_at = response['expires_at']

    def send_request(self, messages):
        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {self.token_}',
        }

        data = {
            "model": self.model_,
            "profanity_check": True,
            "messages": messages,
        }
        response = requests.request("POST",
                                    self.url_request_,
                                    json=data,
                                    headers=headers,
                                    verify=False,
                                    )
        result = response.json()
        print(result)
        return result['choices'][0]['message']['content']


    def generate(self, message):
        try:
            messages_for_api = [{"role": "system", "content": self.system_prompt}]
            current_date = datetime.now().strftime("%Y-%m-%d")
            message_with_date = f"{message}\n\nТекущая дата: {current_date}"
            messages_for_api.append({"role": "user", "content": message_with_date})
            response = self.send_request(messages_for_api)
            return response
        except:
            return "Ошибка во время генерации. Мы уже работаем над исправлением!"


if __name__ == "__main__":
    from uuid import uuid4
    from langchain_core.messages import HumanMessage

    agent = Agent()
    thread_id = f"cli-{uuid4().hex[:6]}"
    config = {"configurable": {"thread_id": thread_id}}

    print("Готово. Пиши сообщение. Команды: /exit (выход), /reset (сброс памяти).")
    print(f"(thread_id: {thread_id})")

    while True:
        try:
            user = input("Саша: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nВыход.")
            break

        if not user:
            continue
        if user.lower() in ("/exit", "/quit"):
            print("Выход.")
            break
        if user.lower().startswith("/reset"):
            thread_id = f"cli-{uuid4().hex[:6]}"
            config = {"configurable": {"thread_id": thread_id}}
            print(f"Память сброшена. Новый thread_id: {thread_id}")
            continue

        try:
            state = agent.agent_.invoke({"messages": [HumanMessage(content=user)]}, config)
            _safe_print("Бот:", state["messages"][-1].content)

        except Exception as e:
            print(f"Ошибка ответа: {e}")