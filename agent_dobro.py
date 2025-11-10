import json
import requests
import os
from uuid import uuid4

HISTORY_LENGHT = 10
with open('cfg.json', 'r') as f:
    data = json.load(f)
    API_KEY = data['GigaChat_API_Key']


class LLMclient:

    def __init__(self, system_prompt):
        self.system_prompt = system_prompt
        self.authorization_key_ = API_KEY
        self.model_ = "GigaChat-2"
        self.url_request_ = "https://gigachat.devices.sberbank.ru/api/v1/chat/completions"
        self.url_auth_ = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
        self.conversation_history = []
        self.get_giga_auth()

    def get_giga_auth(self, verbose=False, team=False):
        rquid = str(uuid4())
        payload = "scope=GIGACHAT_API_CORP" if team else "scope=GIGACHAT_API_PERS"
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
        return result['choices'][0]['message']['content']

    def load_history(self, user_id) -> dict:
        path_to_file = f'history/hisory_{user_id}.json'
        if os.path.exists(path_to_file):
            try:
                with open(path_to_file, 'r', encoding='utf-8') as f:
                    conversation_history = json.load(f)                      
                    return conversation_history
            except Exception as e:
                print(f"Ошибка загрузки истории: {e}")

    def save_history(self, user_id):
        HISTORY_FILE = f'history/{user_id}.json'
        try:
            with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
                json.dump(self.conversation_history[-HISTORY_LENGHT: -1], f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"Ошибка сохранения истории: {e}")

    def add_content(self, message):
        self.conversation_history.append({"role": "user", "content": message})
        messages_for_api = [{"role": "system", "content": self.system_prompt}]
        messages_for_api.extend(self.conversation_history)
        return messages_for_api

    def generate(self, message, user_id):
        self.load_history(user_id=user_id)
        messages_for_api = self.add_content(message)
        response = self.send_request(messages_for_api)        
        self.conversation_history.append({"role": "assistant", "content": response})
        self.conversation_history.append({"role": "user", "content": message})
        self.save_history(user_id)

        return response
