# fsm_file_storage.py
import json
from pathlib import Path
from typing import Any, Dict, Union
import threading

class FSMFileStorage:
    def __init__(self, filepath: str = "fsm_data.json"):
        self.filepath = Path(filepath)
        self._lock = threading.Lock()
        self._data: Dict[int, Dict[str, Any]] = self._load()

    def _load(self) -> Dict[int, Dict[str, Any]]:
        if self.filepath.exists():
            try:
                with open(self.filepath, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                    # Ключи в JSON — строки, но user_id в aiomax — int
                    return {int(k): v for k, v in raw.items()}
            except Exception:
                return {}
        return {}

    def _save(self):
        # JSON не поддерживает int-ключи → сохраняем как строки
        with open(self.filepath, "w", encoding="utf-8") as f:
            json.dump(
                {str(k): v for k, v in self._data.items()},
                f,
                ensure_ascii=False,
                indent=2
            )

    def get_state(self, user_id: int) -> Any:
        return self._data.get(user_id, {}).get("state")

    def get_data(self, user_id: int) -> Any:
        return self._data.get(user_id, {}).get("data")

    def change_state(self, user_id: int, new_state: Any):
        with self._lock:
            if user_id not in self._data:
                self._data[user_id] = {"state": None, "data": None}
            self._data[user_id]["state"] = new_state
            self._save()

    def change_data(self, user_id: int, new_data: Any):
        with self._lock:
            if user_id not in self._data:
                self._data[user_id] = {"state": None, "data": None}
            self._data[user_id]["data"] = new_data
            self._save()

    def clear_state(self, user_id: int) -> Any:
        with self._lock:
            old = self._data.get(user_id, {}).get("state")
            if user_id in self._data:
                self._data[user_id]["state"] = None
                self._save()
            return old

    def clear_data(self, user_id: int) -> Any:
        with self._lock:
            old = self._data.get(user_id, {}).get("data")
            if user_id in self._data:
                self._data[user_id]["data"] = None
                self._save()
            return old

    def clear(self, user_id: int):
        with self._lock:
            if user_id in self._data:
                del self._data[user_id]
                self._save()