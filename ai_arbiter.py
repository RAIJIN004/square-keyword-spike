"""ÁRBITRO IA (nivel 2 de la jerarquía).

Nivel 0 (datos, cada 5 min): MCPs — menciones, sentimiento, precio, BTC.
Nivel 1 (reglas): directivas ENTER/WAIT/AVOID, TP/SL, stops, time-stop.
Nivel 2 (IA): solo en CONFLICTOS que las reglas no resuelven:
  - posición en pérdida + régimen BTC volteado en contra (caso ETH: bear local vs BTC bull que lo empujó a pérdidas)
  - sentimiento empatado con precio en contra
  - relevancia muerta pero en pérdida (¿esperar o cortar?)

La IA recibe el parte estructurado y devuelve HOLD o CLOSE con motivo.
Sin API key → HOLD (las reglas siguen mandando).
"""
import os
import requests

MODEL = os.environ.get("ARBITER_MODEL", "deepseek/deepseek-chat-v3-0324")
URL = "https://openrouter.ai/api/v1/chat/completions"

SYSTEM = (
    "Eres el árbitro final de un sistema jerárquico de trading de futuros cripto "
    "con cuenta pequeña (2-4 USDT, apalancamiento alto). Las reglas mecánicas ya hablaron; "
    "tú solo resuelves el conflicto que se te presenta. Respondes EXACTAMENTE así:\n"
    "DECISION: HOLD | CLOSE\n"
    "MOTIVO: <una línea, máximo 20 palabras>\n"
    "Criterio: protege capital. En duda con apalancamiento alto y sin catalizador a favor, CLOSE. "
    "Si la tesis original sigue intacta (sentimiento + régimen BTC a favor) aunque haya ruido, HOLD."
)


def arbitrate(brief: dict) -> dict:
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        return {"decision": "HOLD", "reason": "sin API key, mandan las reglas"}
    lines = [f"{k}: {v}" for k, v in brief.items()]
    body = ("PARTE DE CONFLICTO (decide HOLD o CLOSE):\n" + "\n".join(lines) +
            "\n¿Se mantiene la tesis o se corta la pérdida?")
    try:
        r = requests.post(URL, headers={"Authorization": f"Bearer {key}",
                                         "Content-Type": "application/json",
                                         "HTTP-Referer": "https://github.com/RAIJIN004/square-keyword-spike",
                                         "X-Title": "RumorBurst-Arbiter"},
                          json={"model": MODEL,
                                "messages": [{"role": "system", "content": SYSTEM},
                                             {"role": "user", "content": body}],
                                "max_tokens": 120, "temperature": 0}, timeout=40)
        txt = r.json()["choices"][0]["message"]["content"].upper()
        decision = "CLOSE" if "DECISION: CLOSE" in txt or "\nCLOSE" in txt else "HOLD"
        motivo = ""
        for ln in txt.splitlines():
            if "MOTIVO" in ln:
                motivo = ln.split(":", 1)[-1].strip().capitalize()
        return {"decision": decision, "reason": motivo or "sin motivo parseable",
                "model": MODEL}
    except Exception as e:
        return {"decision": "HOLD", "reason": f"fallo árbitro ({e}), mandan las reglas"}
