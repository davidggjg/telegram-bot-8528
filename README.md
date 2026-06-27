# Telegram Stream Server — גרסה מתוקנת (ארכיטקטורה מפושטת)

## השינוי הגדול בגרסה הזו

**הוסר ה-userbot, ה-SESSION_STRING, וכל מנגנון ה-copy/forward ל-Saved Messages.**

הבוט (`bot_client`) כבר מחובר ב-MTProto (לא Bot API HTTP) כי הוא משתמש
ב-Pyrogram. ה-20MB הוא מגבלה של שכבת ה-HTTP בלבד (`api.telegram.org/bot.../getFile`)
— Pyrogram מדבר ישירות עם שרתי MTProto של טלגרם ולא כפוף לה. אז אין שום
צורך להעביר את הקובץ לחשבון נפרד — הבוט מזרים ישירות מההודעה המקורית
שבה הוא קיבל את הקובץ (`message.chat.id` / `message.id`).

זה מבטל לגמרי:
- את הבאג `'NoneType' object has no attribute 'id'`
- את הצורך ב-SESSION_STRING וחשבון משתמש נפרד
- את כל בעיות ה-timing/polling של Saved Messages

## מה עוד תוקן (מגרסאות קודמות)

1. **Dockerfile** מאזין ל-`$PORT` בפועל של Render, לא לפורט מקובע.
2. **BASE_URL** מתגלה אוטומטית מ-`RENDER_EXTERNAL_URL`.
3. בדיקת משתני סביבה חסרים עם הודעת שגיאה ברורה בעלייה.

## פריסה ל-Render

### Blueprint (מומלץ)
1. דחוף את התיקייה ל-GitHub repo
2. Render → **New → Blueprint** → חבר repo → ימולא אוטומטית מ-`render.yaml`
3. תתבקש למלא 3 ערכים: `API_ID`, `API_HASH`, `BOT_TOKEN`
4. Deploy

### Web Service ידני
1. **New → Web Service** → חבר repo, Environment: **Docker**
2. Environment Variables: `API_ID`, `API_HASH`, `BOT_TOKEN`
3. Health Check Path: `/ping`

## משתני סביבה נדרשים

| שם | הסבר |
|---|---|
| `API_ID` | מ-https://my.telegram.org |
| `API_HASH` | מ-https://my.telegram.org |
| `BOT_TOKEN` | טוקן הבוט מ-@BotFather |
| `BASE_URL` | **אופציונלי** — רק לעקיפת הגילוי האוטומטי |

> שימי לב: `API_ID`/`API_HASH` כאן הם רק בשביל ש-Pyrogram יוכל להתחבר
> כבוט דרך MTProto (חובה גם לבוטים, לא רק למשתמשים) — זה לא קשור לחשבון
> משתמש אישי.

## הערה על Render Free tier

השירות נכבה אחרי כ-15 דקות חוסר פעילות חיצונית ועולה תוך 20-30 שניות
בבקשה הראשונה. ה-keep-alive בקוד מקטין את הסיכוי לזה, אבל לאפס השבתות
צריך תוכנית בתשלום (Starter ומעלה).
