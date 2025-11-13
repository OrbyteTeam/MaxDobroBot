# bot_main.py
import aiomax
import asyncio
import json
import logging
import sys
import time
import urllib3
import faulthandler
from aiomax import fsm
# from aiomax.fsm import FSMStorage
# from aiomax import WebAppInfo

from fsm_file_storage import FSMFileStorage

# Создаём постоянное хранилище


# Передаём его в бота


from agent import *
from langchain_core.messages import HumanMessage

from vision import *

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

faulthandler.enable()

vision_llm = ClassifierLlm()

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

# bot = aiomax.Bot(TOKEN, default_format="markdown")
bot = aiomax.Bot(TOKEN, default_format="markdown")
fsm_storage = FSMFileStorage("fsm_data.json")
bot.storage = fsm_storage

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


# @bot.on_message(aiomax.filters.equals("/files"))
# async def show_files(message: aiomax.Message, cursor: fsm.FSMCursor):


@bot.on_message()
async def on_message(message: aiomax.Message, cursor: fsm.FSMCursor):
    user_id = str(message.sender.user_id)
    text = message.content or ""

    if message.content == "/files":
        data = cursor.get_data() or {}
        files = data.get("uploaded_files", [])
        if not files:
            await message.reply("У вас пока нет загруженных файлов.")
        else:
            lines = [f"{i+1}. {url}" for i, url in enumerate(files)]
            await message.reply("Ваши файлы:\n" + "\n".join(lines))
        return

    if message.content == "/score":
        data = cursor.get_data() or {}
        score = data.get("score", 0)
        await message.reply(f"Ваше количество очков: {score}")
        return

    if message.body.attachments:
        try:
            
            for doc in message.body.attachments:
                current_data = cursor.get_data() or {}
                uploaded_files = current_data.get("uploaded_files", [])
                score = current_data.get("score", 0)
                print(type(doc))
                if type(doc) != aiomax.types.FileAttachment and type(doc) != aiomax.types.PhotoAttachment:
                    await message.reply("❌ Не удалось сохранить файл. Допустимы только фото и файлы.", attachments=doc)
                    continue
                
                file_url = doc.url
                msg_first = await message.send("Обрабатываю ваш запрос...", attachments=doc)
                info = vision_llm.check_doc(file_url=file_url)
                await msg_first.delete()
                print(info["classification"])
                
                if info["classification"]['is_volunteer_proof'] == True:
                    uploaded_files.append(file_url)
                    score += info["classification"]["hours"]
                    cursor.change_data({"uploaded_files": uploaded_files,"score": score})
                    await message.reply(f"✅ Документ успешно сохранён в вашем профиле!\nНачислено волонтерских часов: {info["classification"]["hours"]}\nТеперь у вас всего часов: {score}", attachments=doc)
                else:
                    await message.reply(f"❌ Документ не прошел проверку!\nПричина: {' '.join(info["classification"]["reasons"])}\nВолонтерских часов: {score}", attachments=doc)

        except Exception as e:
            logging.exception("Ошибка при обработке вложения")
            await message.reply("❌ Не удалось сохранить файл.")
        return


    if text.strip() == "/upload":
        upload_url = f"http://192.168.1.137:8080/?user_id={user_id}"
        kb = aiomax.buttons.KeyboardBuilder()
        kb.add(aiomax.buttons.LinkButton("Открыть ссылку мини-апп", upload_url))
        kb.add(aiomax.buttons.WebAppButton("Открыть мини-апп", "t268_hakaton_bot"))

        await message.send(
            f"Загрузите файл через mini-app", keyboard=kb
         
        )
        return
        


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