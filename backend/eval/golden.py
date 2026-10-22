"""Golden set for the Kora Naturals demo policy (v2).

Each case is labelled by what a careful brand reviewer should flag under the
written guideline -- NOT by what the engine currently does. Known misses are
left in on purpose so the numbers stay honest.

expected item: (rule_key, fact_key or None, excerpt). An empty excerpt means an
insertion-point finding (e.g. a missing disclosure tag), matched on rule only.
"""

CASES = [
    # ---- claims: supported (should pass)
    dict(id="sup-01", channel="linkedin", author="human", text="Our Aloe Calm Gel is made with 96% naturally derived ingredients.", expect=[]),
    dict(id="sup-02", channel="web", author="human", text="Aloe Calm Gel is ₹349 for 100 ml.", expect=[]),
    dict(id="sup-03", channel="email", author="human", text="Free shipping on orders above ₹499.", expect=[]),
    dict(id="sup-04", channel="instagram", author="human", text="Over 2 lakh customers love Kora Naturals.", expect=[]),
    dict(id="sup-05", channel="web", author="human", text="Up to 24 hours of hydration.", expect=[]),
    dict(id="sup-06", channel="linkedin", author="human", text="Aloe Calm Gel comes in a 100% recyclable PET bottle and is cruelty-free.", expect=[]),
    dict(id="sup-07", channel="web", author="human", text="Rose Glow Serum is made with 92% naturally derived ingredients.", expect=[]),
    dict(id="sup-08", channel="instagram", author="human", text="Use code GLOW10 for 10% off Rose Glow Serum.", expect=[]),
    dict(id="sup-09", channel="linkedin", author="human", text="Kora Naturals now ships to more than 19,000 PIN codes across India.", expect=[]),
    # ---- claims: contradicted / expired
    dict(id="con-01", channel="instagram", author="agency", text="Aloe Calm Gel is 100% natural.", expect=[("C-1", "F-07", "100% natural")]),
    dict(id="con-02", channel="web", author="human", text="Aloe Calm Gel now costs just ₹299.", expect=[("C-4", "F-15", "₹299")]),
    dict(id="con-03", channel="web", author="human", text="Rose Glow Serum is ₹549.", expect=[("C-4", "F-25", "₹549")]),
    dict(id="con-04", channel="email", author="human", text="Free shipping on orders above ₹399.", expect=[("C-4", "F-18", "₹399")]),
    dict(id="con-05", channel="instagram", author="agency", text="Join 3 lakh happy customers.", expect=[("C-5", "F-21", "3 lakh")]),
    dict(id="con-06", channel="web", author="human", text="Aloe Calm Gel hydrates for up to 72 hours.", expect=[("C-5", "F-20", "72")]),
    dict(id="con-07", channel="linkedin", author="human", text="Kora Naturals ships to over 25,000 PIN codes.", expect=[("C-5", "F-19", "25,000")]),
    dict(id="con-08", channel="web", author="human", text="Rose Glow Serum is made with 96% naturally derived ingredients.", expect=[("C-5", "F-24", "96%")]),
    dict(id="con-09", channel="instagram", author="influencer", text="Use code GLOW20 for 20% off. #ad", expect=[("C-4", None, "20%")]),
    # ---- claims: unsupported / banned
    dict(id="uns-01", channel="instagram", author="agency", text="Try our eco-friendly gel today.", expect=[("C-1", None, "eco-friendly")]),
    dict(id="uns-02", channel="linkedin", author="human", text="Kora Naturals is India's most trusted skincare brand.", expect=[("C-3", None, "most trusted")]),
    dict(id="uns-03", channel="web", author="human", text="Our biodegradable packaging protects the planet.", expect=[("C-1", None, "biodegradable")]),
    dict(id="uns-04", channel="linkedin", author="human", text="Kora Naturals is carbon neutral.", expect=[("C-1", None, "carbon neutral")]),
    dict(id="uns-05", channel="web", author="human", text="Our organic aloe is grown in Kerala.", expect=[("C-1", None, "organic")]),
    dict(id="uns-06", channel="instagram", author="agency", text="Our gel is the only gel you'll ever need.", expect=[("C-3", None, "only gel")]),
    dict(id="uns-07", channel="web", author="human", text="Dermatologist recommended and gentle on skin.", expect=[("C-0", None, "Dermatologist recommended")]),
    dict(id="ban-01", channel="instagram", author="influencer", text="This gel cures acne overnight. #ad", expect=[("C-2", None, "cures")]),
    dict(id="ban-02", channel="web", author="human", text="Rose Glow Serum heals sun damage.", expect=[("C-2", None, "heals")]),
    dict(id="ban-03", channel="web", author="human", text="Clinically proven to reduce redness.", expect=[("C-2", None, "Clinically proven")]),
    dict(id="ban-04", channel="email", author="human", text="Aloe Calm Gel is gentle and 100% safe.", expect=[("C-2", None, "100% safe")]),
    # ---- terminology
    dict(id="ter-01", channel="web", author="human", text="Our chemical-free formula is gentle.", expect=[("T-4", None, "chemical-free")]),
    dict(id="ter-02", channel="instagram", author="agency", text="Get fairer skin with Rose Glow Serum.", expect=[("T-8", None, "fairer skin")]),
    dict(id="ter-03", channel="web", author="human", text="Our skin whitening range is here.", expect=[("T-8", None, "whitening")]),
    dict(id="ter-04", channel="linkedin", author="human", text="Kora naturals launches a new serum.", expect=[("T-1", None, "Kora naturals")]),
    dict(id="ter-05", channel="instagram", author="human", text="Meet the new Aloe Gel.", expect=[("T-2", None, "Aloe Gel")]),
    dict(id="ter-06", channel="web", author="human", text="Our cheap and cheerful gel.", expect=[("T-5", None, "cheap")]),
    dict(id="ter-07", channel="web", author="human", text="The best anti-ageing serum.", expect=[("T-7", None, "anti-ageing"), ("C-3", None, "best")]),
    dict(id="ter-08", channel="web", author="human", text="Shop the new skin-care edit.", expect=[("T-6", None, "skin-care")]),
    # ---- voice
    dict(id="voi-01", channel="web", author="human", text="Leverage our cutting-edge formula.", expect=[("V-1", None, "Leverage"), ("V-1", None, "cutting-edge")]),
    dict(id="voi-02", channel="web", author="human", text="Hide those embarrassing flaws.", expect=[("V-2", None, "embarrassing"), ("V-2", None, "flaws")]),
    dict(id="voi-03", channel="instagram", author="human", text="Ladies, this one is for you.", expect=[("V-3", None, "Ladies")]),
    dict(id="voi-04", channel="instagram", author="agency", text="Hurry, last chance to buy!", expect=[("V-4", None, "Hurry"), ("V-4", None, "last chance")]),
    dict(id="voi-05", channel="instagram", author="agency", text="BUY NOW before stock runs out.", expect=[("V-4", None, "BUY NOW"), ("A-2", None, "BUY"), ("A-2", None, "NOW")]),
    dict(id="voi-06", channel="email", author="human", text="Offers like this won't wait for anyone, so decide fast.", expect=[("V-4", None, "decide fast")]),
    # ---- accessibility + channel
    dict(id="a11-01", channel="email", author="human", text="Click here to read the full story.", expect=[("A-4", None, "Click here")]),
    dict(id="a11-02", channel="instagram", author="human", text="Loved it!!! So soft!!", expect=[("A-3", None, "!!!"), ("A-3", None, "!!")]),
    dict(id="a11-03", channel="web", author="human", text="![](hero.png) New look, same gel.", expect=[("A-5", None, "![](hero.png)")]),
    dict(id="a11-04", channel="instagram", author="human", text="Calm skin, calm mind 🌿🌸✨💧🫧", expect=[("A-6", None, "💧🫧")]),
    dict(id="cha-01", channel="instagram", author="influencer", text="Loving my Rose Glow Serum glow 🌸", expect=[("P-1", None, "")]),
    dict(id="cha-02", channel="instagram", author="influencer", text="Loving my Rose Glow Serum glow 🌸 #ad", expect=[]),
    dict(id="cha-03", channel="x", author="human", text=("Small change, real impact. Aloe Calm Gel now comes in a 100% recyclable PET bottle, and our refill pilot is "
                                                          "rolling out to Bengaluru stores this month. We'd love your feedback on the new pump, the label and the texture. "
                                                          "Tell us what you think and we'll share what we learn in a follow-up post next week."), expect=[("P-2", None, "")]),
    # ---- clean controls
    dict(id="cln-01", channel="web", author="human", text="Find your new everyday gel on our website.", expect=[]),
    dict(id="cln-02", channel="instagram", author="human", text="A calm routine for warm evenings.", expect=[]),
    dict(id="cln-03", channel="email", author="human", text="Hi there, thanks for being part of the Kora Naturals community.", expect=[]),
]
