# تشغيل المشروع على جهازك (Windows)

المشروع يعمل الآن داخل بيئة سحابية، ولربط Canva يجب تشغيله على جهازك لأن عنوان
`127.0.0.1:8000` يشير إلى جهازك أنت.

## 1) المتطلبات
- Python 3.11 أو أحدث (من python.org، فعّل خيار Add to PATH)
- Git

## 2) تحميل الكود
```
git clone https://github.com/mfathycampus/project-marketing-os.git
cd project-marketing-os
git checkout claude/charming-brown-rrrbc9
```

## 3) ملف .env
انسخ `.env.example` إلى `.env` في **مجلد المشروع الرئيسي** (بجانب README.md):
```
copy .env.example .env
notepad .env
```
عبّئ هذه القيم:
```
ANTHROPIC_API_KEY=...
ENCRYPTION_KEY=...        (انظر الأمر أدناه)
DESIGN_PROVIDER=canva     (أو html للاختبار بدون Canva)
CANVA_CLIENT_ID=...
CANVA_CLIENT_SECRET=...
```
لتوليد ENCRYPTION_KEY:
```
python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())"
```
(نفّذه بعد خطوة 4.) الملف `.env` لا يُرفع إلى Git، ولا ترسل محتواه لأحد.

## 4) التثبيت والتشغيل
```
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -e .[dev]
playwright install chromium
alembic upgrade head
uvicorn app.main:app --port 8000
```

## 5) اختبار Canva
افتح في المتصفح الذي سجلت به الدخول إلى Canva:
- http://127.0.0.1:8000/api/v1/canva/connect  ← وافق على الأذونات
- http://127.0.0.1:8000/api/v1/canva/status   ← أرسل لي الناتج
وثائق API التفاعلية: http://127.0.0.1:8000/docs
