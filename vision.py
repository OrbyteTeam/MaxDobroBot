import os
import re
import mimetypes
from io import BytesIO
import json
import requests
import urllib3
from gigachat import GigaChat
from PIL import Image


urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)



with open("cfg.json", "r", encoding="utf-8") as f:
    data = json.load(f)

API_KEY = data["GigaChat_API_Key"]


class ClassifierLlm:
    def __init__(
        self,
        *,
        credentials = API_KEY,
        is_corp = data["is_corp"],
        model = "GigaChat-2-Max",
        verify_ssl_certs = False,
        request_timeout = 15,
        language: str = "ru",
    ):
        self._language = language
        self._timeout = request_timeout
        self._ua = "ClassifierLlm/1.0 (+max-dobro-bot)"
        

        self._client = GigaChat(
            credentials=API_KEY,
            scope="GIGACHAT_API_CORP" if is_corp else "GIGACHAT_API_PERS",
            model=model,
            verify_ssl_certs=verify_ssl_certs,
            profanity_check=False,
        )

        self._classifier = GigaChat(
            credentials=API_KEY,
            scope="GIGACHAT_API_CORP" if is_corp else "GIGACHAT_API_PERS",
            model="GigaChat-2-Max",
            verify_ssl_certs=verify_ssl_certs,
            profanity_check=False,
        )

    def check_doc(self, file_url: str, *, prompt_path: str = "prompts/system_prompt_classifier") -> dict:
        """
        Сначала формируе текстовое описание, потом по текстовому описанию возвращает вердикт.
        """
        description = self.describe(file_url)

        system_prompt = self._load_classifier_prompt(prompt_path)

        result = self._classifier.chat({
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"ОПИСАНИЕ_ИЗОБРАЖЕНИЯ:\n{description}"}
            ],
            "temperature": 0.0
        })

        choice = result.choices[0]
        if getattr(choice, "finish_reason", "") == "blacklist":
            raise RuntimeError("Классификатор заблокирован модерацией (blacklist).")

        raw_text = (choice.message.content or "").strip()
        data = json.loads(raw_text)

        # normalized = {
        #     "is_volunteer_proof": bool(data.get("is_volunteer_proof", False)),
        #     "confidence": float(data.get("confidence", 0.0)),
        #     "hours": int(data.get("confidence", 0)),
        #     "category": str(data.get("category", "other")),
        #     "reasons": list(data.get("reasons", [])),
        #     "missing_or_suspicious": list(data.get("missing_or_suspicious", [])),
        #     "needs_clarification": list(data.get("needs_clarification", [])),
        # }

        return {
            "description": description,            # описание изображения
            "classification": data,          # JSON-вердикт
            "raw_model_text": raw_text             # сырой текст модели 
        }


    def describe(self, file_url):
        file_like, filename = self._download_image(file_url)
        uploaded = self._client.upload_file(file_like)

        prompt = self._build_prompt()

        result = self._client.chat(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": prompt,
                        "attachments": [uploaded.id_],
                    }
                ],
                "temperature": 0.1,
            }
        )

        choice = result.choices[0]
        if getattr(choice, "finish_reason", "") == "blacklist":
            return "Не удалось описать изображение: ответ заблокирован модерацией."
        text = (getattr(choice.message, "content", "") or "").strip()
        if not text:
            raise RuntimeError("Модель вернула пустое описание.")
        return text

    def _download_image(self, url: str) -> tuple[BytesIO, str]:
        try:
            resp = requests.get(
                url,
                timeout=self._timeout,
                verify=False,
                headers={"User-Agent": self._ua},
                stream=True,
            )
        except Exception as e:
            raise RuntimeError(f"Не удалось скачать изображение: {e}") from e

        if resp.status_code != 200:
            raise RuntimeError(f"HTTP {resp.status_code}: не удалось скачать изображение")

        try:
            img = Image.open(BytesIO(resp.content))
            if img.mode not in ("RGB", "RGBA"):
                img = img.convert("RGB")
        except Exception as e:
            raise RuntimeError(f"Скачанный файл не распознан как изображение: {e}")

        out = BytesIO()
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGB")
        img.save(out, format="PNG")
        out.seek(0)

        try:
            setattr(out, "name", "upload.png")
        except Exception:
            pass

        return out, "upload.png"

    def _build_prompt(self):
        with open("prompts/system_prompt_describer.txt", "r", encoding="utf-8") as f:
            return f.read()

    def _guess_filename(self, url, content_type=None):
        basename = os.path.basename(url.split("?")[0]).strip("/ ")
        if not basename:
            basename = "image"

        ext = os.path.splitext(basename)[1].lower()

        if not ext and content_type:
            guessed = mimetypes.guess_extension(content_type.split(";")[0].strip())
            if guessed:
                ext = guessed

        if not ext:
            ext = ".jpg"

        safe_base = re.sub(r"[^A-Za-z0-9._-]+", "_", os.path.splitext(basename)[0]) or "image"
        return f"{safe_base}{ext}"

    def _load_classifier_prompt(self, path="system/system_prompt_classifier.txt"):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        except Exception:
            return (
                "Ты — строгий классификатор подтверждений волонтёрства. "
                "На вход даётся ОПИСАНИЕ_ИЗОБРАЖЕНИЯ. "
                "Верни JSON с полями: "
                '{"is_volunteer_proof": bool, "confidence": float, '
                '"hours": количество часов волонтерской деятельности (должно быть явно написано, если нет, ставить 0),'
                '"category": "dobro.ru_screenshot|volunteer_book|certificate|other", '
                '"reasons": [str], "missing_or_suspicious": [str], "needs_clarification": [str]} '
                "— без лишнего текста."
            )



if __name__=="__main__":
    clf = ClassifierLlm()
    res = clf.check_doc("https://i.oneme.ru/i?r=BTGBPUwtwgYUeoFhO7rESmr8WMFatQUPBJ8yI289t4rcZdfamHaZdE6b7s-EmHsI5Ew")
    print(res)