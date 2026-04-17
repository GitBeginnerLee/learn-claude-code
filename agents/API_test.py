

'''

测试API调用情况
'''


from openai import OpenAI
from dotenv import load_dotenv
import os
import json

load_dotenv()  # 👈 关键一步


client = OpenAI(
    # 若没有配置环境变量，请用百炼 API Key 将下行替换为：api_key="sk-xxx"
    api_key=os.getenv("OPENAI_API_KEY"),
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
)

response = client.responses.create(
    model="qwen3.6-plus",
    input="给我一个简单的回复，让我知道API调用成功"
)

# 获取模型回复（格式化为 JSON，便于查看）
response_json = response.model_dump()
print(json.dumps(response_json, ensure_ascii=False, indent=2))