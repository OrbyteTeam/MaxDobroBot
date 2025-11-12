# bot_main.py
import aiomax
import asyncio
import json
import logging
import sys
import time
import urllib3
import faulthandler

from agent import *
from langchain_core.messages import HumanMessage

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

faulthandler.enable()

def _invoke_sync(agent_obj: Agent, text: str, config: dict):
    t0 = time.perf_counter()
    print("INVOKE SYNC: start", flush=True)
    try:
        state = agent_obj.agent_.invoke(
            {"messages": [HumanMessage(content=text)]},
            config
        )
        dt = time.perf_counter() - t0
        print(f"INVOKE SYNC: done in {dt:.2f}s", flush=True)
        return state
    except Exception as e:
        dt = time.perf_counter() - t0
        print(f"INVOKE SYNC: EXC after {dt:.2f}s -> {e}", flush=True)
        raise


async def invoke_with_timeout(agent_obj: Agent, text: str, config: dict, timeout: float = 40.0):
    faulthandler.dump_traceback_later(timeout, repeat=False)
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_invoke_sync, agent_obj, text, config),
            timeout=timeout
        )
    finally:
        faulthandler.cancel_dump_traceback_later()


def _ensure_text(x) -> str:
    if isinstance(x, str):
        return x
    try:
        return str(x)
    except Exception:
        return "Не удалось сформировать ответ."


with open("cfg.json", "r", encoding="utf-8") as f:
    data = json.load(f)
TOKEN = data["Token_MAX"]

bot = aiomax.Bot(TOKEN, default_format="markdown")

agent = Agent()


@bot.on_bot_start()
async def on_start(pd: aiomax.BotStartPayload):
    try:
        me = await bot.get_me()
        logging.info(f"Logged in as @{getattr(me, 'username', 'unknown')}")
    except Exception:
        logging.exception("bot.get_me() failed")

    await pd.send(
        "Привет! 😊 Я твой помощник в мире волонтерства.\n"
        "Могу:\n"
        "🔍 Найти интересные события\n"
        "🗓️ Подобрать по дате и времени\n"
        "🏙️ Показать варианты в твоём городе\n"
        "Напиши, например: «завтра в Москве после 15:00»."
    )


@bot.on_message()
async def on_message(message: aiomax.Message):
    user_id = str(message.sender.user_id)
    text = message.content or ""

    print("----------------")
    print(message.sender.user_id, message.sender.first_name, message.sender.last_name)
    print(text)

    msg = await message.send("Генерирую ответ...")

    config = {"configurable": {"thread_id": user_id}}

    try:
        print("BEFORE to_thread", flush=True)
        state = await invoke_with_timeout(agent, text, config, timeout=40.0)
        print("AFTER to_thread", flush=True)

        final_text = state["messages"][-1].content
        final_text = _ensure_text(final_text)

        if not final_text.strip():
            final_text = "Не получилось сформировать ответ. Попробуйте переформулировать запрос."

    except asyncio.TimeoutError:
        logging.error("invoke timeout")
        final_text = "Ответ готовится слишком долго. Попробуйте ещё раз."
    except Exception:
        logging.exception("Agent invoke failed")
        final_text = "Сервис временно недоступен. Попробуйте позже."

    try:
        await msg.delete()
    except Exception:
        pass

    await message.reply(final_text)


# ===== ЗАПУСК =====
if __name__ == "__main__":
    logging.info("Starting bot...")
    bot.run()