import json, os, sys, base64, urllib.request, wave
model, voice, text, out = sys.argv[1:5]
body = {"contents":[{"parts":[{"text": "Произнеси тёплым, дружелюбным женским голосом, живо и в уверенном быстром темпе, без пауз в начале и конце: " + text}]}],
        "generationConfig":{"responseModalities":["AUDIO"],"speechConfig":{"voiceConfig":{"prebuiltVoiceConfig":{"voiceName":voice}}}}}
req = urllib.request.Request(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
    data=json.dumps(body).encode(), headers={"Content-Type":"application/json","x-goog-api-key":os.environ["GEMINI_API_KEY"]})
try:
    d = json.load(urllib.request.urlopen(req, timeout=120))
except urllib.error.HTTPError as e:
    print("ERR", e.code, e.read()[:300].decode(errors="ignore")); sys.exit(1)
part = d["candidates"][0]["content"]["parts"][0]["inlineData"]
pcm = base64.b64decode(part["data"])
rate = int(part["mimeType"].split("rate=")[1].split(";")[0]) if "rate=" in part["mimeType"] else 24000
with wave.open(out, "wb") as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate); w.writeframes(pcm)
print("ok", out, round(len(pcm)/2/rate,2), "s")
