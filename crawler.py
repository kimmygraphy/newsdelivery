"""뉴스보이 데일리 TOP10 크롤러 → JSON 저장 + Discord 전송"""
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

URL = "https://newsboy.news/news-list/daily-top10"
KST = ZoneInfo("Asia/Seoul")
DATA_DIR = Path("data")
# 순위 숫자, 'N위', '뉴스N건 분석', 아이콘 텍스트 등 제목이 아닌 조각들
NOISE = re.compile(r"^(\d+|\d+위|play|분석|뉴스\s*\d+건(\s*분석)?)$")


def fetch_top10():
    headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "ko-KR,ko;q=0.9",
    }
    for attempt in range(3):
        try:
            res = requests.get(URL, headers=headers, timeout=(10, 60))
            break
        except requests.exceptions.RequestException as e:
            print(f"시도 {attempt + 1} 실패: {e}")
            if attempt == 2:
                raise
            import time; time.sleep(15)
    res.raise_for_status()
    soup = BeautifulSoup(res.text, "html.parser")

    items, seen = [], set()
    for a in soup.find_all("a", href=re.compile(r"/news/\d+")):
        url = urljoin(URL, a["href"])
        if url in seen:
            continue
        parts = [p.strip() for p in a.get_text("|").split("|") if p.strip()]
        count = None
        for p in parts:
            m = re.search(r"뉴스\s*(\d+)건", p)
            if m:
                count = int(m.group(1))
                break
        texts = [p for p in parts if not NOISE.match(p)]
        if not texts:
            continue
        seen.add(url)
        items.append({
            "rank": len(items) + 1,
            "title": texts[0],
            "summary": texts[1] if len(texts) > 1 and texts[1] != texts[0] else "",
            "article_count": count,
            "url": url,
        })
        if len(items) == 10:
            break

    period = soup.find(string=re.compile(r"\d+시\s*~\s*\d+시"))
    return items, (period.strip() if period else "")


def save(date_str, period, items):
    DATA_DIR.mkdir(exist_ok=True)
    payload = {
        "date": date_str,
        "period": period,
        "crawled_at": datetime.now(KST).isoformat(timespec="seconds"),
        "items": items,
    }
    (DATA_DIR / f"{date_str}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    # Phase 2 달력용: 데이터가 있는 날짜 목록
    index_path = DATA_DIR / "index.json"
    dates = json.loads(index_path.read_text()) if index_path.exists() else []
    dates = sorted(set(dates) | {date_str})
    index_path.write_text(json.dumps(dates, indent=2), encoding="utf-8")


def send_discord(webhook, date_str, period, items):
    lines = []
    for it in items:
        cnt = f" `{it['article_count']}건`" if it["article_count"] else ""
        lines.append(f"**{it['rank']}.** [{it['title']}]({it['url']}){cnt}")
    embed = {
        "title": f"📰 데일리 TOP10 · {date_str}",
        "description": "\n".join(lines),
        "footer": {"text": f"뉴스보이 {period}".strip()},
        "color": 0x2F6BFF,
    }
    r = requests.post(webhook, json={"embeds": [embed]}, timeout=20)
    r.raise_for_status()


def main():
    date_str = datetime.now(KST).strftime("%Y-%m-%d")
    items, period = fetch_top10()
    if len(items) < 10:
        # 사이트 구조가 바뀌면 실패 처리 → GitHub이 실패 메일을 보내줌
        sys.exit(f"헤드라인을 {len(items)}개만 찾았어요. 파싱 로직 확인 필요.")

    save(date_str, period, items)
    print(f"[{date_str}] {period} 저장 완료")
    for it in items:
        print(it["rank"], it["title"])

    webhook = os.environ.get("DISCORD_WEBHOOK_URL")
    if webhook:
        send_discord(webhook, date_str, period, items)
        print("Discord 전송 완료")


if __name__ == "__main__":
    main()
