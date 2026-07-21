import os
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["LLM_MODEL"] = "llama3.2:3b"
os.environ["OLLAMA_BASE_URL"] = "http://127.0.0.1:9999/v1"
os.environ["KOKORO_VOICE"] = "af_bella"
os.environ["INPUT_DEVICE_INDEX"] = "3"

import config, bot

assert config.llm_model() == "llama3.2:3b"
assert config.ollama_base_url() == "http://127.0.0.1:9999/v1"
assert config.kokoro_voice() == "af_bella"
assert config.input_device_index() == 3

llm = bot.build_llm()
tts = bot.build_tts()
assert llm._settings.model == "llama3.2:3b"
assert str(llm._client.base_url).rstrip("/") == "http://127.0.0.1:9999/v1"
assert tts._settings.voice == "af_bella"
print("OVERRIDE PASS: env -> config -> built services")
