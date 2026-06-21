# Agents Dashboard

דשבורד מקומי (view-only) למעקב אחרי סוכני launchd על מק. הוא מציג בזמן אמת אילו סוכנים רצים, מה הם עשו לאחרונה, מי נכשל ומי נתקע, וקודם כל מה דורש את תשומת ליבך. הכל רץ אצלך על המחשב, בלי שום שרת חיצוני.

> כלי לתלמידי הסדנה. אין כאן שום מפתח, טוקן או נתון אישי. אתה מגדיר את הסוכנים שלך בקובץ `agents.json` והדשבורד מציג אותם.

## מה זה עושה

* סורק את כל קבצי ה-launchd (`~/Library/LaunchAgents/*.plist`) שמתאימים לתחיליות שהגדרת.
* קורא את הלוג של כל סוכן ומסכם בעברית פשוטה מה קרה בריצה האחרונה.
* מסמן: תקין / רץ עכשיו / נכשל / לא רץ מזמן / עוד לא רץ, ומרים למעלה כל מה שצריך טיפול.
* תצוגת טווח תאריכים (היום, אתמול, השבוע, 7/14/30 ימים, או טווח מותאם).
* כפתור "הרץ שוב" לכל סוכן (מפעיל `launchctl kickstart`) וכפתור "סגור התראה".

## דרישות

* macOS (המעקב מבוסס על `launchctl` ו-`~/Library/LaunchAgents`).
* Python 3.9 ומעלה (מגיע מובנה במק). אין תלויות חיצוניות, רק ספריית התקן.

## התקנה והרצה (30 שניות)

```bash
git clone <כתובת-הריפו> agents-dashboard
cd agents-dashboard
python3 server.py
```

פתח בדפדפן: **http://localhost:8420**

בהרצה ראשונה הדשבורד יהיה ריק ("הכל תקין, אין מה לטפל"). זה תקין: הוא מציג רק סוכני launchd אמיתיים שקיימים אצלך תחת `~/Library/LaunchAgents` ושה-Label שלהם מתחיל באחת מ-`include_prefixes`. ברירת המחדל `com.example` לא תואמת לכלום אצלך בכוונה. שלושת הסוכנים ב-`agents.json` הם תבנית להעתקה. שנה את `include_prefixes` לתחילית של הסוכנים שלך (למשל `com.yourname`) והם יופיעו מיד.

## הגדרת הסוכנים שלך: `agents.json`

זה הקובץ היחיד שאתה עורך. מבנה:

```json
{
  "include_prefixes": ["com.example"],
  "exclude_patterns": [],
  "default_error_signatures": ["traceback", "exception", "failed ", "error:"],
  "agents": {
    "com.example.daily-backup": {
      "name": "גיבוי יומי",
      "desc": "מגבה את הקבצים החשובים כל לילה",
      "category": "health",
      "result_hint": "generic",
      "log_path": "~/Library/Logs/example/backup.log",
      "auto_fix": true
    }
  }
}
```

### השדות העליונים

| שדה | מה זה |
|---|---|
| `include_prefixes` | רק סוכנים שה-Label שלהם מתחיל באחת התחיליות האלה יוצגו. החלף ל-תחילית שלך (למשל `com.yourname`). |
| `exclude_patterns` | טקסטים שאם מופיעים ב-Label, הסוכן יוסתר (גם אם עבר את התחילית). |
| `default_error_signatures` | מילים שאם מופיעות בלוג נחשבות לשגיאה. אפשר לדרוס per-agent. |

### השדות של כל סוכן

המפתח של כל סוכן הוא ה-Label המדויק שלו ב-launchd (`com.example.daily-backup`).

| שדה | חובה | מה זה |
|---|---|---|
| `name` | כן | השם שיוצג בעברית. |
| `desc` | לא | תיאור קצר של מה הסוכן עושה. |
| `category` | כן | אחת מ: `content`, `ads`, `health`, `reports`, `אחר`. רק חמש אלה מקבלות כותרת ומוצגות. |
| `result_hint` | לא | איך לפענח את הלוג (ראה למטה). ברירת מחדל: `generic`. |
| `log_path` | לא | נתיב ללוג. תומך ב-`~` וב-`*` (גלוב, נבחר הקובץ העדכני). אם לא מוגדר, נלקח מה-`StandardOutPath` שב-plist. |
| `auto_fix` | לא | אם `true`, המנהל האוטומטי (אופציונלי) רשאי להפעיל מחדש את הסוכן כשהוא נכשל. |
| `error_signatures` | לא | מילות שגיאה ספציפיות לסוכן הזה (דורסות את ברירת המחדל). |
| `state_file` | לא | רלוונטי ל-`result_hint: "article_writer"`. |
| `marker_glob` | לא | רלוונטי ל-`result_hint: "marker_file"`. |

### ערכי `result_hint`

| ערך | מתי להשתמש |
|---|---|
| `generic` | ברירת מחדל. לוקח את השורה האחרונה המשמעותית בלוג. |
| `nl_report` | לוג עם שורת `Result: ...`. |
| `python_freeform` | פלט חופשי של סקריפט פייתון. |
| `line_prefixed` | לוג עם שורות "Skipping" / "Already succeeded today". |
| `marker_file` | הצלחה מסומנת בקובץ marker (צריך `marker_glob`). |
| `article_writer` | מפרסם תוכן ושומר URL ב-`state_file`. |

### לוח הזמנים

הדשבורד לא קובע לו"ז. הוא קורא אותו מתוך ה-plist של כל סוכן (`StartInterval` או `StartCalendarInterval`) ומציג אותו ("יומי 07:00", "כל 30 דק'" וכו'). זה גם מה שקובע מתי סוכן נחשב "לא רץ מזמן".

## הרצה אוטומטית ברקע (מומלץ)

כדי שהדשבורד יעלה לבד עם הדלקת המחשב ויישאר חי:

```bash
bash install-launchagent.sh
```

הסקריפט כותב launchd job עם הנתיבים שלך (בלי שמות משתמש מקודדים), טוען אותו ומאמת. להסרה:

```bash
launchctl bootout gui/$(id -u)/com.example.agents-dashboard
```

## המנהל האוטומטי (אופציונלי): `manager.py`

מעבר לתצוגה, יש "מנהל" שעובר על הסוכנים, מפעיל מחדש את אלה שמסומנים `auto_fix` כשהם נכשלים (עם תקרת ניסיונות ו-cooldown, ואימות שהתיקון תפס), ומסכם את השאר.

```bash
python3 manager.py --dry-run   # בדיקה, לא נוגע בכלום
python3 manager.py             # מריץ באמת
python3 manager.py --silent    # מתקן בשקט, בלי לשלוח התראה
```

ברירת מחדל הדוח מודפס למסך. כדי לקבל אותו כהודעת וואטסאפ דרך בוט משלך, הגדר שני משתני סביבה (הכלי לא כולל בוט, רק שולח אליו):

```bash
export AGENTS_WA_URL="http://localhost:7654/group/send"   # endpoint שמקבל {"jid","text"}
export AGENTS_WA_JID="<היעד>"
```

## טסטים

```bash
python3 -m unittest discover -s tests -v
```

## מבנה הקבצים

| קובץ | תפקיד |
|---|---|
| `server.py` | שרת ה-HTTP והדשבורד (פורט 8420). |
| `status_engine.py` | גילוי סוכנים, קריאת plist, גזירת סטטוס. |
| `extractors.py` | פענוח לוגים לפי `result_hint`. |
| `render.py` | רינדור ה-HTML והניסוח בעברית. |
| `manager.py` | מנהל אוטומטי אופציונלי (תיקון + סיכום). |
| `agents.json` | ההגדרות שלך. הקובץ היחיד שעורכים. |
| `tests/` | בדיקות יחידה. |

---

נבנה על ידי יהב רובין לתלמידי הסדנה. חופשי לשימוש ולשינוי.
