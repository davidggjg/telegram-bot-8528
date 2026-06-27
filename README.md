# Telegram Stream Server — גרסה מתוקנת

## מה תוקן בגרסה הזו?

1. **Dockerfile היה מקבע פורט 8000** ומתעלם מ-`$PORT` שRender מזריק לקונטיינר.
   תוקן ל-`CMD ["sh", "-c", "uvicorn main:api --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]`.

2. **BASE_URL** היה נופל ל-`http://localhost:8000` כברירת מחדל, מה שגרם
   לקישורים שנשלחים למשתמשים להיות לא תקינים. כעת הקוד מגלה את הכתובת
   האמיתית אוטומטית מ-`RENDER_EXTERNAL_URL` (משתנה שRender מגדיר לבד לכל
   Web Service) — **אין צורך להגדיר את BASE_URL בעצמך ב-Render.**

3. **באג `'NoneType' object has no attribute 'id'`** — Pyrogram מחזיר
   לפעמים `None` מ-`copy_message` כששולחים הודעה ל-Saved Messages של עצמך
   (כי טלגרם מחזיר `UpdateShortSentMessage` במקום `UpdateNewMessage`).
   נוספה פונקציית `copy_to_saved_messages` שמטפלת במקרה הזה ומשלימה
   באמצעות שליפה ישירה מההיסטוריה.

4. **בדיקת משתני סביבה חסרים** — אם `API_ID` / `API_HASH` / `SESSION_STRING`
   / `BOT_TOKEN` לא מוגדרים, השירות נכשל בעלייה עם הודעה ברורה בלוגים
   במקום `KeyError` עמום.

5. `get_me()` נטען פעם אחת בעלייה במקום בכל קובץ שמתקבל.

## פריסה ל-Render

### אופציה א׳ — Blueprint (מומלץ, חד-פעמי)
1. דחוף את התיקייה הזו ל-GitHub repo
2. ב-Render: **New → Blueprint** → חבר את ה-repo → Render יקרא את `render.yaml` אוטומטית
3. תתבקש למלא 4 ערכים: `API_ID`, `API_HASH`, `SESSION_STRING`, `BOT_TOKEN`
4. Deploy

### אופציה ב׳ — Web Service ידני
1. **New → Web Service** → חבר repo
2. Environment: **Docker** (יזהה את ה-Dockerfile אוטומטית)
3. Environment Variables (Advanced):
   - `API_ID`
   - `API_HASH`
   - `SESSION_STRING`
   - `BOT_TOKEN`
4. Health Check Path: `/ping`
5. Create Web Service

## משתני סביבה נדרשים

| שם | הסבר |
|---|---|
| `API_ID` | מ-https://my.telegram.org |
| `API_HASH` | מ-https://my.telegram.org |
| `SESSION_STRING` | session string של חשבון המשתמש (היוזר-בוט) |
| `BOT_TOKEN` | טוקן הבוט מ-@BotFather |
| `BASE_URL` | **אופציונלי** — רק אם רוצים לעקוף את הגילוי האוטומטי |

## הערה על Render Free tier

בתוכנית החינמית השירות נכבה אחרי כ-15 דקות חוסר פעילות חיצונית, ועולה מחדש
תוך 20-30 שניות בבקשה הראשונה (cold start). מנגנון ה-keep-alive בקוד שולח
פינג כל 5 דקות כדי לצמצם את זה, אבל אם רוצים אפס השבתות — צריך לעבור
לתוכנית בתשלום (Starter ומעלה).
