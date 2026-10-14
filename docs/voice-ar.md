# الصوت العربي للفيديوهات

يختار النظام مزوّد الصوت تلقائيًا (`VOICE_PROVIDER=auto`) بهذا الترتيب:

| الأولوية | المزوّد | متى يُستخدم | ملاحظات |
|---|---|---|---|
| 1 | **ElevenLabs** | عند ضبط `ELEVENLABS_API_KEY` و`ELEVENLABS_VOICE_ID` | أفضل جودة. الاستخدام التجاري يتطلب خطة مدفوعة (Starter فأعلى) |
| 2 | **Azure Speech** | عند ضبط `AZURE_SPEECH_KEY` و`AZURE_SPEECH_REGION` | رسمي (docs/azure-speech-ar.md) |
| 3 | **edge-tts (مجاني)** | دائمًا، بلا مفتاح | نفس أصوات Microsoft عبر خدمة القراءة في متصفح Edge. **غير رسمي**: قد يتوقف دون إنذار؛ عندها يُنتج الفيديو بلا صوت ولا يفشل |

في صفحة الحملة يظهر المزوّد الحالي: «صوت الفيديو: مجاني (edge-tts) · ar-SA-HamedNeural».
لإيقاف الصوت: `VOICE_PROVIDER=off`. ولفرض مزوّد: `VOICE_PROVIDER=edge` أو `azure` أو `elevenlabs`.

## 1) المجاني (edge-tts): لا يحتاج شيئًا
بعد `update.bat` ثم `start.bat` (يثبّت الحزمة)، اضغط **إعادة التصميم** على أي فيديو فيُقرأ بصوت `ar-SA-HamedNeural`.
لتغيير الصوت شغّل **`set-voice.bat`** واكتب أحد هذه الأسماء (ثم السرعة، مثل `+10%`، أو Enter للتخطي):
`ar-SA-HamedNeural` · `ar-SA-ZariyahNeural` · `ar-AE-HamdanNeural` · `ar-AE-FatimaNeural` · `ar-KW-FahedNeural` ·
`ar-KW-NouraNeural` · `ar-QA-MoazNeural` · `ar-QA-AmalNeural` · `ar-BH-AliNeural` · `ar-OM-AbdullahNeural`

## 2) ElevenLabs (الجودة الأعلى)
1. أنشئ حسابًا في https://elevenlabs.io . للاستخدام في إعلانات منشورة اشترك في خطة **Starter** أو أعلى
   (المجاني لا يمنح رخصة تجارية ويشترط ذكرهم).
2. **اختر صوتًا عربيًا:** Voices ← **Voice Library** ← فلتر Language = Arabic (يمكن البحث عن Saudi / Gulf / Khaleeji)،
   استمع، ثم **Add to my voices**.
3. **المفتاح:** الإعدادات (أيقونة الحساب) ← **API Keys** ← **Create API Key**، وفعّل صلاحيتي
   *Text to Speech* و*Voices (Read)*، وانسخه (يظهر مرة واحدة).
4. شغّل **`set-elevenlabs.bat`**: الصق المفتاح (لا يظهر)، فتظهر قائمة الأصوات في حسابك بمعرّفاتها؛ انسخ
   `voice_id` للصوت الذي أضفته والصقه.
5. `start.bat` ثم **إعادة التصميم**. يظهر: «صوت الفيديو: ElevenLabs · <المعرّف>».

**الاستهلاك:** كل حرف = رصيد تقريبًا؛ الفيديو نحو 100–200 حرف. الصوت يُحفظ على جهازك، فإعادة توليد الفيديو نفسه
لا تستهلك رصيدًا. عند نفاد الرصيد تظهر رسالة واضحة على المنشور.
