import json
import requests
import os
from uuid import uuid4

class LLMclient:

    def __init__(self):
        self.history_length = None
        self.system_prompt = None
        self.authorization_key_ = None
        self.model_ = None
        self.url_request_ = None
        self.url_auth_ = None

    def set_config(self, path_to_config:str):
        with open(path_to_config, 'r') as f:
            data = json.load(f)
            self.authorization_key_ = data['GigaChat_API_Key']
            self.url_request_ = data["url_request"]
            self.url_auth_ = data["url_auth"]
            self.model_ = data["GigaChat_model"]
            self.is_corp = data["is_corp"]
            self.history_length = data["history_length"]
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
        # print(result)
        return result['choices'][0]['message']['content']

    def load_history(self, user_id) -> list:
        path_to_file = f'history/{user_id}.json'
        if os.path.exists(path_to_file):
            try:
                with open(path_to_file, 'r', encoding='utf-8') as f:
                    chat_history = json.load(f)
                    return chat_history
            except Exception as e:
                print(f"Ошибка загрузки истории: {e}")
        else:
            return []

    def save_history(self, user_id, chat_history:list):
        path_to_file = f'history/{user_id}.json'
        try:
            with open(path_to_file, 'w', encoding='utf-8') as f:
                print("save hist:",chat_history)
                json.dump(chat_history[-self.history_length:], f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"Ошибка сохранения истории: {e}")

    def generate(self, message, user_id):
        try:
            chat_history = self.load_history(user_id=user_id)
            print("loaded:", chat_history)
            chat_history.append({"role": "user", "content": message})
            print("loaded_1:", chat_history)


            messages_for_api = [{"role": "system", "content": self.system_prompt}]
            messages_for_api.extend(chat_history)
            response = self.send_request(messages_for_api)

            chat_history.append({"role": "assistant", "content": response})
            print("loaded_2:", chat_history)


            self.save_history(user_id, chat_history)

            return response
        except:
            return "Ошибка во время генерации. Мы уже работаем над исправлением!"
