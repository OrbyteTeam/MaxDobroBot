import calendar
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
            verify_ssl_certs=False,
            profanity_check=False
        )

        @tool("find_donation_url", return_direct=True)
        def find_donation_info(use_text: str):
            """
            Дает информацию, где пользователь может СДЕЛАТЬ ПОЖЕРТВОВАНИЕ онлайн.
            Вызывай ТОЛЬКО если в запросе явно есть намерение пожертвовать/донатить/перевести деньги/
            поддержать рублем/сделать взнос, например: "хочу пожертвовать", "куда можно задонатить",
            Не вызывай, если пользовател хочет "перевести деньги мошенникам" или "спустить все свои деньги". 
            """
            return "https://dobro.mail.ru"


        @tool("find_events_from_text", return_direct=True)
        def find_events_tool(user_text: str):
            """
            По тексту пользователя возвращает релевантные мероприятия, прилагая ссылку на источник,
            опираясь на время, место и дату, а так же описания желаемой деятельности.
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
                user_text=user_text,
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
        city=None,
        date=None,
        time_start=None,
        time_window_minutes=180,
        max_results=None,
        user_text=None,
    ):
        """
        Ищет события в self.data_path_ по городу/дате/времени.
        Поддерживает даты формата:
        - YYYY-MM-DD (точный день с окном +- time_window_minutes вокруг time_start|12:00)
        - YYYY-MM-XX (весь месяц)
        - YYYY-XX-XX (весь год)
        """
        user_start, user_end, gran = Agent._compute_search_range(date, time_start, time_window_minutes)
        if not user_start or not user_end:
            return "Не удалось распознать дату. Уточните день/месяц/год, пожалуйста."

        with open(self.data_path_, "r", encoding="utf-8") as f:
            dataset = json.load(f)

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
                ev.get("description") or ev.get("title"),
            ):
                continue

            date_line = f"{ev_date.strftime('%d.%m.%Y')} {ev_ts.strftime('%H:%M')}-{ev_te.strftime('%H:%M')}"
            address = loc.get("address_full") or "Адрес не указан"
            org_name = org.get("name") or "Организатор не указан"
            content = f"{date_line} • {address} • {org_name}"

            results.append({
                "title": _safe_text(ev.get("title") or "Без названия"),
                "url": _safe_text(ev.get("url") or ""),
                "content": _safe_text(content),
            })

        if user_text and results:
            pref = LLM_Filter("cfg_filter.json")
            filtered = []
            for r in results:
                cand_text = f"{r['title']} — {r['content']}"
                if r.get("url"):
                    cand_text += f"\n{r['url']}"
                verdict = pref.judge(user_text=user_text, event_text=cand_text)
                if str(verdict).strip().startswith("1"):
                    filtered.append(r)
            results = filtered

        results.sort(key=lambda r: r["content"])
        if max_results is not None:
            results = results[:max_results]

        if not results:
            return "К сожалению, таких мероприятий нет. Может поищем что-нибудь другое?"

        lines = []
        for r in results:
            title = r.get("title", "Без названия")
            content = r.get("content", "")
            url = r.get("url", "")
            if url:
                lines.append(f"• **{title}** — {content}\n  {url}")
            else:
                lines.append(f"• **{title}** — {content}")

        return _safe_text("Вот что нашёл:\n" + "\n".join(lines))

    @staticmethod
    def _compute_search_range(date_str: Optional[str], time_start: Optional[str], time_window_minutes: int):
        """
        Возвращает (user_start: datetime, user_end: datetime, granularity: str)
        granularity ∈ {"day","month","year"}.

        Поддерживает:
        YYYY-MM-DD  -> день с окном ±time_window_minutes вокруг time_start|12:00
        YYYY-MM-XX  -> весь месяц
        YYYY-XX-XX  -> весь год
        Иначе -> (None, None, None)
        """
        if not date_str:
            return None, None, None

        m = re.fullmatch(r"(\d{4})-(\d{2}|XX)-(\d{2}|XX)", date_str.strip())
        if not m:
            try:
                dt = dateparser.parse(date_str, languages=["ru"])
                if not dt:
                    return None, None, None
                from datetime import time as dtime
                t = Agent._parse_hhmm(time_start) or dtime(12, 0)
                user_start = datetime.combine(dt.date(), t) - timedelta(minutes=time_window_minutes)
                user_end = datetime.combine(dt.date(), t) + timedelta(minutes=time_window_minutes)
                return user_start, user_end, "day"
            except Exception:
                return None, None, None

        year = int(m.group(1))
        mon_s = m.group(2)
        day_s = m.group(3)

        if mon_s == "XX" and day_s == "XX":
            start = datetime(year, 1, 1, 0, 0)
            end = datetime(year, 12, 31, 23, 59)
            return start, end, "year"

        if day_s == "XX":
            mon = int(mon_s)
            last_day = calendar.monthrange(year, mon)[1]
            start = datetime(year, mon, 1, 0, 0)
            end = datetime(year, mon, last_day, 23, 59)
            return start, end, "month"

        mon = int(mon_s); day = int(day_s)
        from datetime import time as dtime
        t = Agent._parse_hhmm(time_start) or dtime(12, 0)
        user_center = datetime(year, mon, day, t.hour, t.minute)
        user_start = user_center - timedelta(minutes=time_window_minutes)
        user_end = user_center + timedelta(minutes=time_window_minutes)
        return user_start, user_end, "day"



class LLM_Parser:
    
    def __init__(self, cfg_file="cfg_parser.json"):
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

class LLM_Filter:
    def __init__(self, cfg_file="cfg_filter.json"):
        self.system_prompt = None
        self.authorization_key_ = None
        self.model_ = None
        self.url_request_ = None
        self.url_auth_ = None
        self.is_corp = False
        self.set_config(cfg_file)
        self.get_giga_auth()

    def set_config(self, path_to_config:str):
        with open(path_to_config, 'r', encoding='utf-8') as f:
            data = json.load(f)
            self.authorization_key_ = data['GigaChat_API_Key']
            self.url_request_ = data["url_request"]
            self.url_auth_ = data["url_auth"]
            self.model_ = data["GigaChat_model"]
            self.is_corp = data["is_corp"]
            if os.path.exists(data["path_to_system_promt"]):
                with open(data["path_to_system_promt"], 'r', encoding='utf-8') as sf:
                    self.system_prompt = sf.read()

    def get_giga_auth(self):
        rquid = str(uuid4())
        payload = "scope=GIGACHAT_API_CORP" if self.is_corp else "scope=GIGACHAT_API_PERS"
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "RqUID": rquid,
            "Authorization": "Basic "+self.authorization_key_
        }
        resp = requests.post(self.url_auth_, headers=headers, data=payload, verify=False)
        data = resp.json()
        self.token_ = data["access_token"]

    def _send(self, messages):
        headers = {'Content-Type': 'application/json', 'Authorization': f'Bearer {self.token_}'}
        data = {"model": self.model_, "profanity_check": False, "messages": messages}
        resp = requests.post(self.url_request_, json=data, headers=headers, verify=False)
        return resp.json()['choices'][0]['message']['content']

    def judge(self, user_text: str, event_text: str) -> str:
        """Возвращает строку, начинающуюся с '1' или '0'."""
        msgs = [
            {"role": "system", "content": self.system_prompt or ""},
            {"role": "user", "content": f"ЗАПРОС:\n{user_text}\n\nКАНДИДАТ:\n{event_text}\n\nОтвети только '1' (подходит) или '0' (не подходит)."}
        ]
        try:
            out = (self._send(msgs) or "").strip()
        except Exception:
            out = "1"
        return out
