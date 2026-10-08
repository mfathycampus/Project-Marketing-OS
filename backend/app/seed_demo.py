"""Create a demo project + campaign without calling an LLM:  python -m app.seed_demo"""
from app.ai.orchestrator import AIOrchestrator
from app.ai.provider import FakeProvider
from app.ai.schemas import CampaignRequest
from app.db import SessionLocal
from app.models import AudienceProfile, BrandProfile, BrandRule, Product, Project

ITEMS = [
    ("offer_square", "عرض نهاية الأسبوع للعائلات", "وجبة عائلية تكفي الجميع", "49 ريال", "اطلب الآن"),
    ("quote_square", "طعم يجمع العائلة", "من مطبخنا إلى مائدتكم بكل حب", "", "اطلب الآن"),
    ("offer_square", "ثنائي الشاورما", "ساندوتشان وبطاطس ومشروب", "29 ريال", "اطلب الآن"),
    ("announcement_story", "توصيل سريع في الرياض", "نوصل طلبك ساخنًا إلى باب بيتك", "", "اطلب الآن"),
    ("offer_square", "ليلة الجمعة المميزة", "مشويات مشكلة لأربعة أشخاص", "99 ريال", "احجز طاولتك"),
]


def main() -> None:
    with SessionLocal() as s:
        p = Project(name="مطاعم المذاق (تجريبي)")
        p.brand_profile = BrandProfile(name="مطاعم المذاق", description="مطعم عائلي في الرياض", tone="ودية ودافئة",
                                       language="ar", dialect="خليجي", primary_color="#0F766E", secondary_color="#F59E0B")
        p.audience_profile = AudienceProfile(demographics="عائلات في الرياض", interests=["الأكل الجماعي"],
                                             pain_points=["ضيق الوقت"], goals=["وجبة سريعة لذيذة"])
        p.products = [Product(name="وجبة عائلية", price="49 ريال", description="تكفي 4 أشخاص", features=["مشويات", "أرز"])]
        p.rules = [BrandRule(kind="forbidden_word", text="مجاني")]
        s.add(p)
        s.commit()
        data = {
            "name": "حملة نهاية الأسبوع", "objective": "increase_orders",
            "strategy": "التركيز على العروض العائلية والتوصيل السريع طوال الأسبوع.",
            "content_items": [
                {"platform": "instagram", "day_offset": i, "headline": h, "caption": f"{h}\n{d}", "cta": c,
                 "design": {"template_key": t, "fields": {"description": d, **({"price": pr} if pr else {})}}}
                for i, (t, h, d, pr, c) in enumerate(ITEMS)
            ],
        }
        req = CampaignRequest(objective="increase_orders", duration_days=7, post_count=len(ITEMS), platforms=["instagram"])
        AIOrchestrator(s, FakeProvider([data])).generate_campaign(p.id, req)
        print(f"Demo project created. Open http://127.0.0.1:8000/ui/#/p/{p.id}")


if __name__ == "__main__":
    main()
