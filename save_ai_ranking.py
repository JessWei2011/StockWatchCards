"""Save the currently audited data.js TOP 5 into ai_rankings.json."""

import argparse

from reports_manager_server import canonical_top5_cards, upsert_ai_ranking


def build_entry(ai_name):
    cards = canonical_top5_cards(ai_name)
    if len(cards) != 5:
        raise ValueError(f"data.js 內 {ai_name} 的獨立 aiAnalysis 分數不足 5 筆；禁止借用其他 AI 結果")
    if not all(card.get("verified") is True for card in cards):
        raise ValueError(f"{ai_name} TOP 5 尚未全部通過自己的第二次稽核")
    dates = [str(card.get("date") or "") for card in cards]
    date = max(dates)
    if not date:
        raise ValueError("TOP 5 缺少分析日期")

    top5 = []
    audit_top5 = []
    for rank, card in enumerate(cards, 1):
        code = str(card.get("code") or "").strip()
        score = card.get("winRate")
        reason = str(card.get("action") or "").strip()
        if not reason:
            reason = f"依 AI_SCORING_RULES.md 完成數據與規則稽核，綜合分數 {score} 分"
        item = {
            "rank": rank,
            "code": code,
            "name": str(card.get("name") or code),
            "score": score,
            "decision": str(card.get("decision") or "續抱觀望"),
            "reason": reason,
        }
        top5.append(item)
        audit_top5.append({"rank": rank, "code": code, "score": score})

    return {
        "date": date,
        "ai": ai_name,
        "top5": top5,
        "audit": {"status": "passed", "issues": [], "top5": audit_top5},
    }


def main():
    parser = argparse.ArgumentParser(description="儲存已通過規則稽核的 AI TOP 5")
    parser.add_argument("--ai", required=True, help="AI 名稱，例如 Gemini 或 ChatGPT")
    args = parser.parse_args()
    saved = upsert_ai_ranking(build_entry(args.ai.strip()))
    summary = "、".join(
        f"#{item['rank']} {item['code']} {item['name']} {item['score']:g}分"
        for item in saved["top5"]
    )
    print(f"RANKING_SAVED {saved['date']} {saved['ai']}：{summary}")


if __name__ == "__main__":
    main()
