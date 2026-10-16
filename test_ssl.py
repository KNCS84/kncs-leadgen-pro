from engine.ai_client import call_ai

result = call_ai('Say OK', max_tokens=5, use_cache=False)
print('SSL fix result:', repr(result))