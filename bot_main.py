import aiomax
import logging
import urllib3
import json
from llm import *

with open('cfg.json', 'r') as f:
    data = json.load(f)
    TOKEN = data['Token_MAX']

bot = aiomax.Bot(TOKEN, default_format="markdown")

@bot.on_bot_start()
async def info(pd: aiomax.BotStartPayload):
    await pd.send("Бот за ярд")

@bot.on_message()
async def echo(message: aiomax.Message):
    print("----------------")
    print(message.sender.user_id, message.sender.first_name, message.sender.last_name)
    print(message.content)

    msg = await message.send("Генерирую ответ...")
    resp = agent.generate(message.content, message.sender.user_id)
    print(resp)
    print("------------------")
    await msg.delete()
    await message.reply(resp)

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

agent = LLMclient()
agent.set_config("cfg.json")
agent.get_giga_auth()
logging.basicConfig(level=logging.INFO)
bot.run()