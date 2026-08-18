# -*- coding: utf-8 -*-
"""
诊断台 · DeepSeek 客户端（标准库实现，无第三方依赖）
环境变量 DEEPSEEK_API_KEY 必需；单次诊断 LLM 调用上限 ≤10 次由 orchestrator 强制
"""
import json
import os
import urllib.request

API_URL = "https://api.deepseek.com/chat/completions"
MODEL = "deepseek-chat"

# 价格（元/千 token，用于落库成本 tracing；按 DeepSeek 官网定价，变更时改这里）
PRICE_IN, PRICE_OUT = 0.002, 0.008


def call_deepseek(messages, temperature=0.2, max_tokens=4000, json_mode=False):
    key = os.environ.get("DEEPSEEK_API_KEY")
    if not key:
        raise RuntimeError("未设置 DEEPSEEK_API_KEY 环境变量（export DEEPSEEK_API_KEY=sk-...）")
    body = {"model": MODEL, "messages": messages,
            "temperature": temperature, "max_tokens": max_tokens}
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    req = urllib.request.Request(API_URL, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json",
                                          "Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read())
    usage = data.get("usage", {})
    cost = usage.get("prompt_tokens", 0) / 1000 * PRICE_IN + usage.get("completion_tokens", 0) / 1000 * PRICE_OUT
    text = data["choices"][0]["message"]["content"]
    return {"text": text, "usage": usage, "cost_yuan": round(cost, 6)}
