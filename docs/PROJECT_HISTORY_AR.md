# Project Marketing OS — السجل الكامل لما أُنجز

> وثيقة مرجعية بكل ما عملنا عليه من الفكرة الأولى حتى آخر خطوة: القرارات، البنية، ما بُني، المشكلات التي واجهناها وحلولها، وما بقي.
> المستودع: `mfathycampus/project-marketing-os` — الفرع: `claude/charming-brown-rrrbc9` — آخر حالة: 26 commit، 77 اختبارًا ناجحًا.

---

## 1. الفكرة والهدف

نظام لإنشاء وإدارة المحتوى التسويقي لمشاريع متعددة:

- **Claude** هو عقل المحتوى والاستراتيجية.
- **محرك تصميم** يحوّل المحتوى إلى صور جاهزة (Canva أو مولّد HTML).
- **اعتماد ← جدولة ← نشر** على منصات التواصل.
- المنصات مجرد **Adapters** خارج النواة، فيمكن إضافة منصة جديدة بلا لمس بقية النظام.

المسار الذي وصلنا إليه فعليًا:

```
مشروع ← هوية العلامة (Brand Brain) ← طلب حملة
   ← Claude يولّد حملة منظّمة (استراتيجية + منشورات + مواصفات تصميم)
   ← تصميم PNG لكل منشور ← مراجعة واعتماد ← تكييف النص لكل منصة
   ← جدولة ← مجدوِل ← نشر (يدوي مجهّز، أو آلي على فيسبوك)
```

---

## 2. المبادئ المعمارية المعتمدة

1. **PostgreSQL/SQLite مصدر الحقيقة.** أي طابور (تصاميم، نشر) هو جدول في قاعدة البيانات، والـ worker مجرد من يفرّغه. هذا يجعل المهام تنجو من إعادة التشغيل.
2. **نقطة واحدة للتعامل مع Claude:** `AIOrchestrator`. لا استدعاءات `anthropic.messages.create` متناثرة.
3. **مخرجات Claude منظّمة** (Structured Outputs) ومُتحقَّق منها في الكود: حدود كل منصة، الكلمات الممنوعة، عدد المنشورات، مفاتيح القوالب.
4. **الفصل بين المحتوى والنشر:** المحتوى يُعتمد مرة واحدة وينتهي عند `APPROVED`. ما بعده (مجدول، منشور، فاشل) يعيش في جدول `publications` لكل منصة.
5. **المحتوى ← نسخ منصات ← منشورات:** عنصر محتوى واحد، وله نسخ نص لكل منصة (`content_variants`)، ومنشور لكل منصة.
6. **Provider لكل شيء متغير:** `LLMProvider` و`DesignProvider` و`SocialProvider`.
7. **Context Engine:** يبني للنموذج سياقًا مخصصًا للطلب لا كل بيانات المشروع (جزء ثابت قابل للتخزين المؤقت، وجزء متغير).
8. **الأسرار مشفّرة** (Fernet) ولا تُعاد للواجهة، و`.env` خارج Git.
9. **كل انتقال حالة** يُتحقق منه بآلة حالات ويُسجَّل في `content_status_history`.

---

## 3. التقييم الأولي للمواصفات (قبل أي كود)

قُيّمت المواصفات الأولى وأُدخلت عليها التعديلات التالية:

| الملاحظة | القرار |
|---|---|
| Canva Autofill قد لا يكون متاحًا (يتطلب Enterprise عادةً) | `DesignProvider` مجرد، ومولّد HTML كخطة أولى لا تعتمد على Canva |
| 30 جدولًا و4 طبقات DDD ثقيلة لبداية المشروع | Modular Monolith بسيط، وتأجيل الجداول غير الضرورية |
| حالة المحتوى مختلطة بحالة النشر | المحتوى ينتهي عند `APPROVED`، والنشر في `publications` |
| Celery وRedis في البداية | طابور في قاعدة البيانات وworker داخل نفس العملية |
| مراجعات تطبيقات المنصات تستغرق أسابيع | البدء بوضع التطوير (Development mode) لحساباتك أنت |
| جودة العربية يجب اختبارها مبكرًا | اختُبرت على حملة حقيقية بعد Sprint 1 |
| Claude Structured Output | قُصد به tool use ثم تغيّر إلى `output_config.format` (انظر القسم 8) |

### فحص الأسئلة الخمسة (Sprint 0)
1. **Canva Autofill:** وثائق Canva تقول إنه يتطلب Enterprise. حسابك (Education، فريق مدرسة) أعطى لاحقًا `autofill: true`، لكن نشر القوالب محجوب (القسم 7).
2. **من المستخدم:** افتُرض مالكًا واحدًا/وكالة، فلا جدول organizations.
3. **أول منصة:** Meta (فيسبوك + إنستغرام).
4. **اللغة:** العربية أولًا (RTL، حقل لهجة في ملف العلامة).
5. **البناء:** بدأ فعليًا.

---

## 4. المكوّنات التقنية

| الطبقة | الاختيار |
|---|---|
| Backend | Python 3.11+، FastAPI، SQLAlchemy 2، Alembic، Pydantic v2 |
| قاعدة البيانات | SQLite للتشغيل المحلي السريع، PostgreSQL 16 للإنتاج (الـ migrations مجرَّبة على الاثنين) |
| الذكاء الاصطناعي | Anthropic API — النموذج `claude-sonnet-5-5`، مخرجات منظّمة، Prompt caching للسياق الثابت |
| التصميم | مولّد HTML ← PNG عبر Chromium (Playwright)، ومحوّل Canva (OAuth + Autofill) |
| الواجهة | ملفات ثابتة بلا بناء: Preact + htm (محفوظة محليًا في `frontend/vendor`)، عربي RTL، تُخدم من FastAPI على `/ui/` |
| المهام | threadان داخل العملية: `DesignWorker` و`PublishingWorker` |
| التخزين | قرص محلي (`LocalStorage`) بنفس واجهة S3 المستقبلية |
| التشفير | Fernet لتوكنات Canva وMeta |

### هيكل المجلدات

```
project-marketing-os/
├── backend/
│   ├── app/
│   │   ├── ai/            context.py, orchestrator.py, provider.py, schemas.py, prompts/
│   │   ├── api/           routes.py, design_routes.py, publishing_routes.py, meta_routes.py, schemas.py
│   │   ├── content/       state_machine.py, service.py
│   │   ├── designs/       registry.py, service.py, worker.py, html_renderer.py, factory.py, templates/, canva/
│   │   ├── models/        project, campaign, design, publication, social, ai_run
│   │   ├── publishing/    service.py, worker.py
│   │   ├── social/        provider.py, meta.py
│   │   ├── storage/       local.py
│   │   ├── config.py, db.py, main.py, migrate.py, diagnose.py, seed_demo.py
│   ├── migrations/versions/   0001 … 0007
│   └── tests/             77 اختبارًا
├── frontend/              index.html, app.js, styles.css, vendor/
├── docs/                  decisions.md, local-setup-ar.md, meta-setup-ar.md, PROJECT_HISTORY_AR.md
├── docker-compose.yml, .env.example, README.md
```

### جداول قاعدة البيانات (16 جدولًا) والـ migrations

| Migration | المحتوى |
|---|---|
| 0001 | `projects`, `brand_profiles`, `audience_profiles`, `products`, `brand_rules`, `ai_runs`, `campaigns`, `content_items` |
| 0002 | `design_jobs`, `design_assets`, `canva_connections`، وألوان العلامة |
| 0003 | `content_status_history`، و`campaigns.start_date`، و`content_items.planned_date` |
| 0004 | `publications` |
| 0005 | `social_connections`, `meta_pending` |
| 0006 | `content_variants` |
| 0007 | `social_connections.last_error` |

### آلة حالات المحتوى

```
DRAFT ↔ AI_GENERATED → DESIGN_PENDING → DESIGN_READY → PENDING_APPROVAL → APPROVED
                                              ↘ REJECTED → DRAFT       ARCHIVED
```
الانتقالات غير المنطقية مرفوضة (مثلًا `APPROVED → PENDING_APPROVAL`)، والمحتوى المعتمد لا يُعدَّل.

### حالات النشر (`publications`)

```
scheduled → publishing → published | awaiting_manual | failed
failed → scheduled (إعادة محاولة)    scheduled/awaiting_manual/failed → cancelled
```
إعادة المحاولة تلقائية بتأخير 5 دقائق × المحاولة، حتى 3 محاولات. أخطاء الصلاحية/الإعداد تفشل فورًا بلا إعادة.

---

## 5. ما بُني، مفصّلًا

### 5.1 إدارة المشاريع وBrand Brain
- مشروع: إنشاء، تعديل، أرشفة، حذف، المنطقة الزمنية (افتراضي `Asia/Riyadh`).
- ملف العلامة: الاسم، الوصف، الرسالة، النبرة، اللغة، اللهجة، لونان.
- الجمهور: الديموغرافيا، الاهتمامات، نقاط الألم، الأهداف.
- المنتجات (اسم، سعر، وصف، مزايا) وقواعد العلامة (`forbidden_word`, `must_do`, `must_not`).
- نقطة `GET /projects/{id}/brand-brain` تعيد كل ذلك.

### 5.2 توليد الحملات (`AIOrchestrator.generate_campaign`)
- المدخلات: الهدف، المدة، عدد المنشورات، المنصات، النبرة، وصف حر، تاريخ البداية.
- الجزء الثابت من السياق (هوية، جمهور، قواعد) يُرسل بـ `cache_control`، والمتغير (المنتجات المناسبة للطلب) بعده.
- المخرجات (`CampaignOut`): اسم، هدف، استراتيجية، ومنشورات (منصة، يوم، عنوان ≤60، نص، دعوة للإجراء، مواصفة تصميم).
- التحقق في الكود: عدد المنشورات، المنصات المطلوبة فقط، اليوم ضمن المدة، مفتاح القالب مسموح، حدود الأحرف لكل منصة (إكس 280...)، الكلمات الممنوعة.
- محاولة إصلاح واحدة تلقائية: يُرسل الخطأ للنموذج ويطلب تصحيحه.
- كل استدعاء يُسجَّل في `ai_runs` (النموذج، نسخة الـ prompt، tokens، cache، الزمن، المخرجات الخام، الخطأ).
- المساعد `_run` مشترك لكل عمليات النموذج.

### 5.3 تكييف المنشور لمنصات أخرى (`adapt_content`)
- يعيد صياغة النص لكل منصة (إنستغرام دافئ ومرئي، فيسبوك حواري، لينكدإن مهني، إكس قصير، تيك توك مرح) مع الحقائق والأسعار نفسها.
- نفس التحقق (حدود، كلمات ممنوعة، منصة لكل نسخة بالضبط) ونفس إعادة المحاولة.
- يمكن النسخ بلا ذكاء اصطناعي. النسخة تُقفل بعد جدولتها.

### 5.4 التصميم
- **القوالب الافتراضية** (HTML): `offer_square`, `quote_square`, `announcement_story`، عربية RTL مع ألوان العلامة. التحقق من الحقول والطول وقصّها لتناسب القالب.
- **`DesignService`:** يسجل `design_jobs` بمفتاح idempotency (منشور + مزوّد + قيم + ألوان) فلا يتكرر التوليد، وينقل الحالة `DESIGN_PENDING → DESIGN_READY` أو يعيدها عند الفشل.
- **Worker التصاميم:** يفرّغ الطابور بحجز ذري، ويتعافى من التوقف (processing ← pending).
- **محوّل Canva:** OAuth 2.0 مع PKCE، تخزين مشفّر، فحص `capabilities` قبل Autofill، قراءة dataset القالب، مهمة Autofill، التصدير. اختُبر بقدر ما أتاحت بيئتنا (انظر القسم 7). مع نقاط اكتشاف القوالب: `/canva/brand-templates`.

### 5.5 الموافقات والتقويم
- إرسال للموافقة، اعتماد، رفض (بسبب)، واعتماد الكل لحملة. كل انتقال في `content_status_history`.
- تقويم شهري وأسبوعي (الأسبوع يبدأ السبت)، تاريخ المنشور = بداية الحملة + `day_offset`، وقابل للتعديل.
- تعديل المنشور (عنوان، نص، دعوة، تاريخ) قبل الاعتماد.

### 5.6 الجدولة والنشر
- `schedule`: للمحتوى المعتمد فقط، ومنصة واحدة مرة واحدة لكل عنصر.
- جدولة الحملة كلها بساعة محلية (الافتراضي 19:00 بتوقيت المشروع، يُخزَّن UTC).
- تعديل موعد منشور مجدول، و**انشر الآن**، وإلغاء، وإعادة محاولة، و«تم النشر» اليدوي مع رابط اختياري.
- **المجدوِل:** حجز ذري للمستحق، إعادة محاولة مع تأخير، تعافٍ بعد التوقف.
- **المزودون:** `manual` (الافتراضي: يجهّز المنشور للنشر اليدوي مع نسخ النص وتنزيل الصورة) و`dryrun` للتجربة، و`meta`.

### 5.7 Meta (فيسبوك + إنستغرام) — وضع التطوير
- تسجيل الدخول عبر OAuth بحالة موقّعة (HMAC) تنجو من إعادة التشغيل، واختيار الصفحة إن تعددت، وحفظ توكن الصفحة مشفّرًا.
- فيسبوك: رفع الصورة مباشرة. إنستغرام: رابط عام للصورة بصيغة JPEG (`PUBLIC_BASE_URL`)، وانتظار معالجة Meta، وفحص النسبة 4:5–1.91:1 قبل الاستدعاء.
- الربط المتصل لمنصة يتغلب على المزود الافتراضي، وما سواه يبقى يدويًا.
- فشل الصلاحية يعلّم الربط «يحتاج إعادة» ويحفظ رسالة Meta الحرفية (`last_error`).
- بحث بديل عن الصفحات الممنوحة (`debug_token`) حين تعيد `/me/accounts` قائمة فارغة.
- `META_SCOPES` قابلة للتعديل دون كود، و`META_CONFIG_ID` لتطبيقات Facebook Login for Business.
- **جُرّب فعليًا:** نُشر منشور على صفحة Maeen بنجاح.

### 5.8 الواجهة (`/ui/`)
المشاريع، هوية العلامة، إنشاء الحملة، صفحة مراجعة الحملة (توليد التصاميم، اعتماد، تعديل، جدولة، المنصات)، التقويم، النشر (ربط الحسابات، النشر اليدوي، تعديل المواعيد). عربية RTL، ومظهر فاتح/داكن تلقائي.

### 5.9 أدوات مساعدة
- `python -m app.diagnose`: يعرض حالة الإعدادات دون إظهار أسرار (ملفات `.env` الموجودة، طول المفاتيح، أسماء المتغيرات، مسافات قبل `=`).
- `python -m app.seed_demo`: مشروع وحملة تجريبية بلا استدعاء Claude.
- تحميل `.env` من جذر المشروع و`backend/`، وتحويل مسار SQLite إلى مجلد `backend` مهما كان مجلد التشغيل، وتشغيل الـ migrations تلقائيًا مع SQLite.

---

## 6. واجهة البرمجة (API v1)

| المجموعة | المسارات |
|---|---|
| المشاريع | `POST/GET /projects`، `GET/PATCH/DELETE /projects/{id}`، `POST /projects/{id}/archive` |
| Brand Brain | `PUT /brand`, `PUT /audience`، `POST/GET /products`، `POST /rules`، `GET /brand-brain`، `DELETE /products/{id}`, `/rules/{id}` |
| الحملات | `POST/GET /projects/{id}/campaigns`، `GET .../campaigns/{cid}`، `POST .../approve-all`، `POST .../designs`، `POST .../schedule-all` |
| المحتوى | `PATCH /content-items/{id}`، `POST .../submit/approve/reject/transition/design`، `GET .../history`، `GET .../design/image` |
| النسخ | `POST /content-items/{id}/variants`، `PATCH/DELETE /variants/{id}` |
| التقويم | `GET /projects/{id}/calendar?start=&end=` |
| النشر | `POST /content-items/{id}/schedule`، `GET /projects/{id}/publications`، `POST /publications/{id}/cancel/retry/reschedule/mark-published` |
| Canva | `GET /canva/connect`, `/callback`, `/status`, `/brand-templates`, `/brand-templates/{id}/dataset` |
| Meta | `GET /meta/status`, `/connect`, `/callback`, `/pending/{id}`, `/check-public-url`، `POST /meta/select`، `GET/DELETE /projects/{id}/social-connections` |

التوثيق التفاعلي: `/docs`.

---

## 7. رحلة Canva (ما وجدناه)

1. **الشرط:** Autofill وBrand Templates تتطلب Enterprise حسب الوثائق. حسابك Education ضمن فريق مدرسة (Ibn Khaldun)، وبعد الربط ظهر `capabilities` يتضمن `autofill` و`brand_template` و`resize` و`background_removal`.
2. **إنشاء تطبيق Canva:** صار «Create an app» (ثم **Outside Canva ← Canva REST APIs**). اشترط حسابك MFA فأضفنا كلمة مرور ثم فعّلنا MFA (مفاتيح المرور Passkeys ليست كلمة مرور).
3. **ما حُجب بقرار مسؤول الفريق:**
   - توليد التصاميم بالذكاء الاصطناعي: `Design generation has been disabled by the brand's admin`.
   - نشر قالب Brand Template: `User does not have permission to publish design ... as a brand template`.
4. **ما وُجد جاهزًا:** تصميمان تجريبيان (برجر وبيتزا) بنص مربوط بحقول `HEADLINE/PRICE/DESCRIPTION/CTA` بشكل سليم، لكنهما لا يُنشران كقالب بدون صلاحية.
5. **القرار:** مولّد HTML هو الافتراضي. وكود Canva جاهز للتفعيل عند توفر قالب منشور (يحتاج صلاحية مدير الفريق أو حسابًا آخر).
6. **غير مؤكد:** مسارات Canva كُتبت من المعرفة لأن موقع المطورين كان محجوبًا من بيئة البناء، وجُرّب منها فعليًا فقط OAuth والتخزين وفحص `capabilities` على حسابك.

---

## 8. سجل المشكلات وحلولها (بالترتيب الزمني)

| # | المشكلة | السبب | الحل |
|---|---|---|---|
| 1 | `Access is denied` عند إنشاء `.venv` وتثبيت الحزم | المشروع استُنسخ داخل `C:\Windows\System32` | نقله إلى `C:\Users\...\projects` |
| 2 | `Activate.ps1` غير معروف | كتابة `venv` بدل `.venv` ثم تنفيذه من المجلد الخطأ | `cd backend` ثم `.venv\Scripts\Activate.ps1`، و`Set-ExecutionPolicy RemoteSigned` عند الحاجة |
| 3 | `No 'script_location'` عند `alembic` | تنفيذه من المجلد الرئيسي | التنفيذ من `backend` |
| 4 | `Errno 10048` (المنفذ مشغول) | نسخة قديمة من الخادم ما زالت تعمل | إيقافها (Ctrl+C أو `Stop-Process`) ثم التشغيل، أو `--reload` |
| 5 | `Internal Server Error` في callback الخاص بـ Canva | `ENCRYPTION_KEY` غير صالح | رسائل خطأ واضحة، ثم اشتقاق مفتاح صالح تلقائيًا من أي قيمة |
| 6 | المفتاح 43 حرفًا بدل 44 | علامة `=` الأخيرة لم تُحفظ في الملف (تاريخ تعديل `.env` أقدم) | أداة `diagnose` أظهرت الحقيقة، وتحمّل الكود للمفاتيح المبتورة |
| 7 | `can't compare offset-naive and offset-aware datetimes` | SQLite يعيد تواريخ بلا منطقة زمنية | تحويلها إلى UTC عند المقارنة |
| 8 | `f-string expression part cannot include a backslash` في `diagnose` | Python 3.11 عند المستخدم أقدم من بيئة الاختبار (3.13) | إعادة كتابة السطر بدون backslash داخل f-string |
| 9 | `tool_choice type "tool" ... not supported for this model` | Claude 5.x يرفض إجبار الأدوات | الانتقال إلى `output_config.format` (JSON Schema)، وجعل `fields` كائنًا مغلقًا، ورفع `max_tokens` إلى 16000 |
| 10 | `no such table: design_jobs` | تشغيل الخادم من المجلد الرئيسي أنشأ `marketing.db` فارغًا | تثبيت مسار SQLite داخل `backend`، وتشغيل الـ migrations تلقائيًا |
| 11 | تبويب النشر يعرض `Not Found` | الواجهة تحدّثت لكن الخادم لم يُعد تشغيله | إعادة التشغيل (الواجهة ملفات ثابتة، والخادم يحمّل الكود مرة) |
| 12 | حقل الوقت لا يغيّر مواعيد المنشورات | يُطبَّق لحظة «جدولة المعتمد» فقط | محرر موعد لكل منشور + «انشر الآن» في صفحة الحملة وتبويب النشر |
| 13 | `Invalid Scopes` من Facebook | صلاحيات لم تُضف إلى التطبيق عبر Use cases | `META_SCOPES` قابلة للتعديل، وإضافة use cases في Meta |
| 14 | `لم تُرجع Meta أي صفحة` | الصفحة لم تُحدَّد في موافقة Facebook (صلاحيات لكل صفحة) | رسالة تشرح السبب، وبحث بديل عبر `debug_token`، وخطوات تعديل الموافقة |
| 15 | «App not active» ثم `developers.facebook.com/r/user/error/` | حساب المطوّر في حالة تأكيد/مراجعة معلّقة عند Meta | الانتظار، وفحص بريد Meta، وتذكرة دعم عند الاستمرار. وبعدها عاد التطبيق يعمل |
| 16 | «انتهى الربط» دون سبب | رفض Meta للنشر (جلسة/صلاحية) | حفظ رسالة Meta الحرفية وعرضها في بطاقة الربط |

### أخطاء أنا ارتكبتها وصُحّحت
- افتراض أن Autofill لن يعمل مع حساب Education. الفحص الفعلي أثبت العكس.
- توجيهك لصفحة تكامل Canva القديمة بينما صارت «Create an app».
- اختبار قديم كُسر عند تعديل مسار، فدُفع مرتين.
- `pkill` أوقف جلسة الأوامر نفسها مرة أثناء الاختبار (خطأ بيئة، لا أثر على المنتج).

---

## 9. الأمان

- الأسرار في `.env` فقط (مستثنى من Git): `ANTHROPIC_API_KEY`, `ENCRYPTION_KEY`, `CANVA_CLIENT_*`, `META_APP_*`.
- توكنات Canva وMeta مشفّرة بـ Fernet، ولا تُعاد للواجهة ولا تظهر في السجلات.
- حالة OAuth لـ Meta موقّعة بـ HMAC مع انتهاء 15 دقيقة، وPKCE لـ Canva.
- مسار التخزين المحلي يمنع الخروج من المجلد (path traversal).
- المخرجات في قوالب HTML تُهرَّب (`escape`).
- **غير موجود بعد:** تسجيل دخول ومستخدمون، فأي شخص يصل للخادم يتحكم بكل شيء. لا تعرّضه للإنترنت قبل ذلك. ولو استخدمت نفقًا لإنستغرام فصور المنشورات متاحة لمن يعرف رابطها.
- **احتفظ بنسخة** من `ENCRYPTION_KEY` و`backend\marketing.db`، فبدونهما تضيع التوكنات والبيانات.

---

## 10. التشغيل على Windows (ملخص)

```powershell
cd C:\Users\<أنت>\projects
git clone https://github.com/mfathycampus/project-marketing-os.git
cd project-marketing-os
git checkout claude/charming-brown-rrrbc9
copy .env.example .env          # ثم عدّله في Notepad
cd backend
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
playwright install chromium
uvicorn app.main:app --port 8000   # الجداول تُنشأ تلقائيًا مع SQLite
```
ثم افتح `http://127.0.0.1:8000/`.

**بعد كل `git pull`:** أوقف الخادم القديم (Ctrl+C) و`pip install -e ".[dev]"` ثم شغّله من جديد. وللتحقق من الإعدادات: `python -m app.diagnose`. وبيانات تجريبية: `python -m app.seed_demo`.

متغيرات `.env` المهمة:

| المتغير | الغرض |
|---|---|
| `DATABASE_URL` | SQLite أو PostgreSQL |
| `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL`, `AI_EFFORT` | الذكاء الاصطناعي |
| `ENCRYPTION_KEY` | تشفير التوكنات |
| `DESIGN_PROVIDER` | `html` (الافتراضي) أو `canva` |
| `CANVA_CLIENT_ID/SECRET`, `CANVA_TEMPLATE_MAP` | Canva |
| `META_APP_ID/SECRET`, `META_REDIRECT_URI`, `META_SCOPES`, `META_CONFIG_ID` | Meta |
| `PUBLIC_BASE_URL` | رابط عام للصور (إنستغرام) |
| `PUBLISH_PROVIDER`, `PUBLISH_DEFAULT_TIME`, `PUBLISH_MAX_ATTEMPTS` | النشر |

---

## 11. حالة التحقق: ما جُرّب حقيقيًا وما لم يُجرَّب

| البند | الحالة |
|---|---|
| توليد حملة بـ Claude (عربي خليجي، أسعار صحيحة) | **جُرّب فعليًا** وأعطى نتيجة جيدة |
| المولّد HTML، الموافقات، التقويم، الجدولة، النشر اليدوي، المجدوِل | **جُرّب** بمتصفح حقيقي وباختبارات |
| ربط Canva (OAuth، `capabilities`) | **جُرّب** على حسابك |
| Autofill وتصدير Canva | لم يُجرَّب (لا قالب منشور) |
| فيسبوك: ربط ونشر على صفحة Maeen | **جُرّب ونجح** مرة |
| فيسبوك: إعادة الربط بعد تعطل التطبيق | **متوقف** عند تحديد الصفحة في موافقة Facebook (القسم 12) |
| إنستغرام | **لم يُجرَّب** (يحتاج حسابًا احترافيًا مربوطًا، صلاحيات، ورابطًا عامًا) |
| تكييف النصوص بالذكاء الاصطناعي لمنصة أخرى | لم يُجرَّب بمفتاح حقيقي |
| PostgreSQL | الـ migrations جُرّبت، والتشغيل الفعلي كان على SQLite |
| Meta وCanva API | مكتوبة من المعرفة (المواقع محجوبة من بيئة البناء)، واختُبرت باستجابات وهمية |

---

## 12. الوضع الحالي بالتفصيل (آخر خطوة)

- تطبيق Meta **maeen-معين** (App ID `1645051160571605`) في وضع Development، وأنت Administrator، ولا تنبيهات ظاهرة.
- تسجيل الدخول يعمل، والصلاحيات الست ممنوحة (`pages_show_list, pages_read_engagement, pages_manage_posts, instagram_basic, instagram_content_publish, public_profile`).
- **المشكلة:** Meta يعيد قائمة صفحات فارغة، والبحث البديل لم يجد صفحات ممنوحة. أي أن الصفحة غير مرتبطة بموافقة التطبيق.
- **الخطوات المطلوبة منك:**
  1. `facebook.com/settings?tab=business_tools` ← التطبيق ← **View and edit** ← حدّد صفحة Maeen واحفظ (أو **Remove** ثم أعد الربط وحدّد الصفحة في النافذة).
  2. إن لم ينجح: Graph API Explorer، `me/accounts?fields=id,name,instagram_business_account` وأرسل لقطة بلا التوكن.
  3. إن كانت الصفحة داخل Business Portfolio: أضف نفسك مديرًا مباشرًا للصفحة، أو أضف صلاحية `business_management`.

---

## 13. المتبقي (الخطوات المقترحة بالترتيب)

1. **إكمال ربط فيسبوك ثم إنستغرام** (القسم 12 وملف `docs/meta-setup-ar.md`).
2. **اختبار تكييف النصوص** بـ Claude الحقيقي على منصات متعددة وضبط الـ prompt.
3. **قياس الأداء:** جلب التفاعل والوصول لكل منشور وتغذيته للحملات التالية.
4. **تسجيل الدخول وتعدد المستخدمين**، ثم **الاستضافة** (Docker، PostgreSQL، تخزين سحابي S3).
5. **لوحة تكلفة واستخدام** من `ai_runs` مع حدود استخدام.
6. **Canva:** عند توفر صلاحية نشر القوالب، يُربط `CANVA_TEMPLATE_MAP` ويُفعَّل `DESIGN_PROVIDER=canva`.
7. **منصات أخرى:** لينكدإن، تيك توك، إكس عبر `SocialProvider`، أو مزوّد وسيط إن كثر العملاء.
8. مراجعة Meta الرسمية إن فُتح النظام لحسابات الآخرين.

### أجزاء من المواصفات الأولى لم تُبنَ (قرارات مقصودة)
- جداول `organizations`, `locations`, `contacts`, `brand_assets`, `documents`, `document_chunks` (وpgvector): تُؤجَّل حتى توجد حاجة فعلية.
- `analytics_snapshots` و`audit_logs` العام (يوجد سجل حالات المحتوى و`ai_runs`).
- Redis/Celery: استُبدلا بطابور في قاعدة البيانات وworker داخل العملية. ينتقل لهما عند تعدد العمليات.
- طبقات DDD الأربع: استُعيض عنها بوحدات أبسط.

---

## 14. دروس مفيدة

- **تحقق من الحقيقة قبل التخمين:** `python -m app.diagnose` وتاريخ تعديل الملف أنهيا أسئلة كثيرة.
- **الخادم لا يتحدث تلقائيًا:** الواجهة ملفات ثابتة تتحدث فور `git pull`، لكن كود الخادم يحتاج إعادة تشغيل.
- **شغّل دائمًا من `backend`**، ولا تفتح روابط `callback` بنفسك، وابدأ كل ربط من الواجهة.
- **لا تلصق أسرارًا** (`ENCRYPTION_KEY`, App Secret) في محادثة أو لقطة.
- **قيود المؤسسات:** حسابات المدارس قد تحجب ميزات (Canva) بقرار المسؤول، فاعتمد خطة بديلة لا تتوقف عليها.
- **الاعتماد على Meta:** حالة التطبيق وحساب المطوّر خارج سيطرتنا، لذلك النشر اليدوي المجهّز يبقى مسارًا يعمل دائمًا.

---

## 15. سجل الـ commits (مختصر)

| المرحلة | الأبرز |
|---|---|
| Sprint 0–1 | هيكل FastAPI، Brand Brain، AIOrchestrator، توليد الحملة، آلة الحالات |
| Sprint 2 | مولّد HTML، تصاميم وjobs، محوّل Canva، ألوان العلامة |
| إصلاحات Canva | `.env` من الجذر، مسار SQLite، اشتقاق المفتاح، تواريخ SQLite، رسائل أخطاء واضحة |
| Sprint 3 | الموافقات، التقويم، worker التصاميم، واجهة عربية RTL، بيانات تجريبية |
| مخرجات منظّمة | استبدال `tool_choice` بـ `output_config.format` |
| Sprint 4 | الجدولة، `publications`، المجدوِل، المزودون، تبويب النشر |
| Meta | OAuth، الربط، فيسبوك/إنستغرام، `META_SCOPES`، الأخطاء الواضحة، البحث البديل |
| تعدد المنصات | `content_variants`، التكييف بالذكاء الاصطناعي، جدولة لكل منصة |
| تحسينات الواجهة | محرر الموعد و«انشر الآن» |

للتفاصيل الدقيقة: `git log` في المستودع، و`docs/decisions.md` للقرارات، و`docs/local-setup-ar.md` و`docs/meta-setup-ar.md` لأدلة الإعداد.
