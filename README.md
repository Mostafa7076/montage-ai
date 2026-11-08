# 🎬 Montage AI

**أداة مونتاج احترافية مفتوحة المصدر بالذكاء الاصطناعي.** تقص الفيديو وتعدّل ألوانه وتضيف الانتقالات والنصوص العربية والموسيقى، ثم تصدّره بدقة تصل إلى 8K. يمكنها أن تترك المونتاج لـ **Claude** أو **Gemini** أو **Grok**، وأن تحوّل أي صورة من **Pinterest** إلى قالب مونتاج، وأن تسلّم العمل إلى **CapCut** و**Premiere Pro** و**DaVinci Resolve**.

*Open-source AI video editor on top of ffmpeg: cuts, color grading, transitions, Arabic titles, up to 8K export. It can hand the edit to Claude, Gemini or Grok, turn Pinterest pins into montage templates, and export to CapCut, Premiere and DaVinci.*

---

## ✨ المميزات

| الميزة | التفاصيل |
|---|---|
| ✂️ القص الذكي | يكتشف تغيّر المشاهد ويقص عندها تلقائياً، ويغيّر السرعة من 0.25× إلى 4× |
| 🎨 تعديل الألوان | السطوع، التباين، التشبع، Gamma، حرارة اللون، Vibrance، توازن الظلال والإضاءة، منحنيات جاهزة، ملفات LUT ‎(.cube)، إطار داكن (Vignette)، حبيبات فيلم، حدة |
| 🔀 الانتقالات | أكثر من 25 انتقالاً: fade، dissolve، slide، wipe، zoom، circle، pixelize... |
| 🔤 النصوص | نصوص عربية وإنجليزية بتشكيل صحيح للحروف، مع ظهور واختفاء تدريجي وخلفية اختيارية |
| 🎵 الصوت | موسيقى مع اختفاء تدريجي، وضبط مستوى الصوت تلقائياً إلى ‎-14 LUFS (معيار يوتيوب وإنستغرام) |
| 📐 المقاسات | 720p، ‏1080p، ‏1440p، ‏4K، ‏8K، عمودي 9:16 (ريلز وتيك توك)، مربع، 4:5. وتعبئة الإطار بالقص، أو بخلفية مموهة، أو بإطار أسود |
| 💎 جودة التصدير | ‏H.264 بجودة CRF 14، و‏H.265 بعمق 10-bit، و‏ProRes 422 HQ، وخيار `gpu` الذي يختار تلقائياً NVIDIA NVENC أو Intel Quick Sync أو AMD AMF |
| ⚡ السرعة | تُجمع كل عمليات الألوان في ملف LUT واحد، وتُطبَّق على كل مقطع قبل تكبيره إلى 4K أو 8K |
| 🤖 الذكاء الاصطناعي | Claude وGemini وGrok يكتبون خطة المونتاج كاملة من طلب مكتوب بأي لغة |
| 📌 Pinterest | الصق رابط Pin، فتقيس الأداة ألوانه، ثم يقرأ الذكاء الاصطناعي الأجواء ويصنع منها قالباً |
| 🔁 التصدير لبرامج أخرى | مجلد جاهز لـ CapCut، وملف XML لـ Premiere وDaVinci، وملف LUT ‎(.cube) |

## 📦 التثبيت

المتطلبات: **Python 3.10+** و**ffmpeg** (الإصدار 7 أو أحدث). لا تحتاج أي مكتبات إضافية.

```bash
winget install Gyan.FFmpeg      # Windows
brew install ffmpeg             # macOS
sudo apt install ffmpeg         # Linux
```

```bash
git clone https://github.com/Mostafa7076/montage-ai.git
cd montage-ai
```

## 🚀 الاستخدام

### الواجهة (الأسهل)

```bash
python -m montage_ai serve
```

تفتح الأداة صفحة في المتصفح على `http://127.0.0.1:8765`. أضف مسارات الفيديو، واختر قالباً أو أنشئ واحداً من Pinterest، واكتب ما تريد، ثم اضغط **أنشئ المونتاج** وبعدها **صدّر الفيديو**. على ويندوز يمكنك أيضاً الضغط مرتين على `run.bat`.

### سطر الأوامر

**مونتاج تلقائي بقالب سينمائي وتصدير 4K:**

```bash
python -m montage_ai make clip1.mp4 clip2.mp4 -t cinematic --resolution 4k --title "My Trip" --music song.mp3 -o final.mp4
```

**الذكاء الاصطناعي يقوم بالمونتاج (Claude أو Gemini أو Grok):**

```bash
python -m montage_ai make *.mp4 --ai claude -p "30s energetic travel reel, warm colors" -t vibrant_reels -o reel.mp4
```

**قالب جديد من Pinterest:**

```bash
python -m montage_ai template from "https://www.pinterest.com/pin/123456/" -n "summer_warm" --ai gemini
python -m montage_ai template list
```

**خطة قابلة للتعديل، ثم تصدير:**

```bash
python -m montage_ai plan a.mp4 b.mp4 -t moody_dark -o plan.json
python -m montage_ai render plan.json -o final.mov --preset prores_hq
```

**تسليم العمل لبرامج أخرى (CapCut، Premiere / DaVinci، LUT):**

```bash
python -m montage_ai export capcut plan.json -o my_capcut_pack
python -m montage_ai export xml plan.json -o sequence.xml
python -m montage_ai export lut plan.json -o look.cube
```

> يمكن كتابة العنوان والطلب (`--title` و `-p`) بالعربية، وستظهر النصوص بالعربية في الفيديو.

أضف `--draft` لمعاينة سريعة قبل التصدير النهائي، و`--preset gpu` لتصدير أسرع بكرت الشاشة.

> **ملاحظة عن السرعة:** التصدير بدقة 4K على المعالج وحده بطيء على الأجهزة الضعيفة. حبيبات الفيلم (`grain`) تحديداً تبطئ الضغط وتكبّر حجم الملف كثيراً. جرّب أولاً `--draft`، ثم صدّر النسخة النهائية بـ `gpu`.

## 🔑 مفاتيح الذكاء الاصطناعي

| المزود | متغير البيئة | النموذج الافتراضي | لتغيير النموذج |
|---|---|---|---|
| Claude | `ANTHROPIC_API_KEY` | `claude-sonnet-5-5` | `MONTAGE_CLAUDE_MODEL` |
| Gemini | `GEMINI_API_KEY` | `gemini-2.5-flash` | `MONTAGE_GEMINI_MODEL` |
| Grok | `XAI_API_KEY` | `grok-4` | `MONTAGE_GROK_MODEL` |

```powershell
# Windows PowerShell
$env:ANTHROPIC_API_KEY = "sk-ant-..."
```

يمكنك أيضاً إدخال المفاتيح من الواجهة. تبقى في الذاكرة فقط، ولا تُحفظ على القرص.

## 📌 كيف تعمل قوالب Pinterest؟

1. تجلب الأداة صورة الـ Pin العامة. إن لم تنجح، انسخ عنوان الصورة نفسها (Copy image address) والصقه.
2. تقيس ffmpeg سطوع الصورة وتباينها وتشبعها ودفء ألوانها وأبرز ألوانها.
3. يرى الذكاء الاصطناعي الصورة إن اخترته، ويحدد الأجواء والألوان ونوع الانتقال وسرعة القص وشكل النص.
4. يُحفظ القالب في `montage_ai/templates/` ويظهر في الواجهة مع صورته.

> الأداة لا تسجّل الدخول إلى Pinterest ولا تجمع الألواح كاملة. تستخدم فقط الصورة العامة للـ Pin الذي تختاره.

## 🔁 CapCut وPremiere

- **CapCut**: لا يوفر CapCut صيغة رسمية لاستيراد المشاريع، لذلك تُصدّر الأداة مجلداً بما يقبله CapCut: مقاطع مرقمة بالترتيب، وملف `look.cube` للألوان (Adjust ثم LUT ثم Import)، وملف `captions.srt` للنصوص، وملف `README_CapCut.txt` فيه الخطوات.
- **Premiere / DaVinci**: ملف XML (FCP7) يحفظ القص والترتيب. استورده عبر File ثم Import. الانتقالات والسرعة لا تنتقل في XML، أما الألوان فتنقلها عبر ملف الـ LUT.

## 🧩 خطة المونتاج (plan.json)

كل شيء في الأداة يمر عبر ملف JSON واحد يمكنك تعديله يدوياً. انظر [examples/plan_example.json](examples/plan_example.json).

## 🗂️ بنية المشروع

```
montage_ai/
  engine.py      محرك ffmpeg: القص، الألوان، الانتقالات، النصوص، التصدير
  plan.py        التحقق من خطة المونتاج
  templates.py   القوالب + المونتاج التلقائي بدون ذكاء اصطناعي
  ai.py          Claude / Gemini / Grok
  reference.py   Pinterest والصور إلى قوالب
  export.py      CapCut و XML و LUT
  server.py      الواجهة المحلية
  static/        صفحة الواجهة
  templates/     القوالب (JSON)
```

## 📄 الترخيص

MIT
