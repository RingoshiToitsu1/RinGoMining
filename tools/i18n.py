"""
Shared translations for RinGoMining: interface text and the reusable page blocks.
English is the source. French uses "vous", Spanish uses "tú", German uses "du".
"""

LANG_ORDER = ["en", "fr", "es", "de"]

LANG_NAMES = {"en": "English", "fr": "Français", "es": "Español", "de": "Deutsch"}
LOCALES = {"en": "en_US", "fr": "fr_FR", "es": "es_ES", "de": "de_DE"}
PREFIX = {"en": "", "fr": "/fr", "es": "/es", "de": "/de"}
HUB_SLUG = {"en": "guides", "fr": "guides", "es": "guias", "de": "ratgeber"}

# Root (hand-written) pages and their address in each language.
ROOT_PAGES = {
    "home": {l: (PREFIX[l] + "/") for l in LANG_ORDER},
    "apply": {l: PREFIX[l] + "/apply.html" for l in LANG_ORDER},
    "signup": {l: PREFIX[l] + "/signup.html" for l in LANG_ORDER},
    "verify": {l: PREFIX[l] + "/verify.html" for l in LANG_ORDER},
    "disclaimer": {l: PREFIX[l] + "/disclaimer.html" for l in LANG_ORDER},
}

MONTHS = {
    "en": ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"],
    "fr": ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre"],
    "es": ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"],
    "de": ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November", "Dezember"],
}


def fmt_date(iso, lang):
    y, m, d = (int(x) for x in iso.split("-"))
    name = MONTHS[lang][m - 1]
    if lang == "en":
        return f"{name} {d}, {y}"
    if lang == "fr":
        return f"{d} {name} {y}"
    if lang == "es":
        return f"{d} de {name} de {y}"
    return f"{d}. {name} {y}"


# Text shown in the "this site is available in your language" banner (in the target language).
SUGGEST = {
    "fr": ("Ce site est disponible en français.", "Voir en français"),
    "es": ("Este sitio está disponible en español.", "Ver en español"),
    "de": ("Diese Seite gibt es auch auf Deutsch.", "Auf Deutsch ansehen"),
}

UI = {
    "en": {
        "nav_guides": "Guides", "copy_title": "Copy code", "lang_label": "Language",
        "foot_home": "Home", "foot_guides": "All guides", "foot_claim": "Claim the RINGO5 bonus", "foot_disclaimer": "Disclaimer",
        "tg": "Telegram", "phone": "Phone", "refcode": "Referral code",
        "foot_p": "RinGoMining is run by Ringo, an independent GoMining ambassador. Not affiliated with, employed by, or speaking for GoMining. Nothing here is financial advice. Mining rewards are not guaranteed and you can lose money.",
        "read_disclaimer": "Read the full disclaimer", "sticky": "Claim my RINGO5 bonus →",
        "crumb_home": "Home", "crumb_guides": "Guides",
        "by": "By Ringo", "by_rest": "10+ years in crypto · mining on GoMining since May 2025",
        "updated": "Updated", "ambassador": "Independent GoMining ambassador, not affiliated with GoMining. Links use my referral code.",
        "faq_kicker": "Quick answers", "faq_h2": "FAQ", "keep_reading": "Keep reading", "read": "Read →",
        "hub_title": "GoMining Guides: Promo Code, Reviews, Profitability & More | RinGoMining",
        "hub_desc": "Straight answers about GoMining from a 10-year crypto veteran who mines on it every day: the RINGO5 promo code, is it legit, real results, profitability, and how to start.",
        "hub_eyebrow": "Straight answers · Code RINGO5", "hub_h1": "Everything I'd tell you about GoMining",
        "hub_lede": "I've been in crypto for 10+ years and mining on GoMining since May 2025. These are the questions people ask me most, answered with my real numbers.",
        "hub_short": "Guides",
    },
    "fr": {
        "nav_guides": "Guides", "copy_title": "Copier le code", "lang_label": "Langue",
        "foot_home": "Accueil", "foot_guides": "Tous les guides", "foot_claim": "Obtenir le bonus RINGO5", "foot_disclaimer": "Avertissement",
        "tg": "Telegram", "phone": "Téléphone", "refcode": "Code de parrainage",
        "foot_p": "RinGoMining est géré par Ringo, ambassadeur GoMining indépendant. Non affilié à GoMining, ni employé par GoMining, ni son porte-parole. Rien ici n'est un conseil financier. Les récompenses de minage ne sont pas garanties et vous pouvez perdre de l'argent.",
        "read_disclaimer": "Lire l'avertissement complet", "sticky": "Obtenir mon bonus RINGO5 →",
        "crumb_home": "Accueil", "crumb_guides": "Guides",
        "by": "Par Ringo", "by_rest": "10+ ans dans la crypto · mine sur GoMining depuis mai 2025",
        "updated": "Mis à jour le", "ambassador": "Ambassadeur GoMining indépendant, non affilié à GoMining. Les liens utilisent mon code de parrainage.",
        "faq_kicker": "Réponses rapides", "faq_h2": "FAQ", "keep_reading": "À lire aussi", "read": "Lire →",
        "hub_title": "Guides GoMining : code promo, avis, rentabilité et plus | RinGoMining",
        "hub_desc": "Des réponses franches sur GoMining par un vétéran de la crypto depuis 10 ans qui mine dessus tous les jours : le code promo RINGO5, fiable ou arnaque, vrais résultats, rentabilité et comment démarrer.",
        "hub_eyebrow": "Réponses franches · Code RINGO5", "hub_h1": "Tout ce que je vous dirais sur GoMining",
        "hub_lede": "Je suis dans la crypto depuis plus de 10 ans et je mine sur GoMining depuis mai 2025. Voici les questions qu'on me pose le plus, avec mes vrais chiffres.",
        "hub_short": "Guides",
    },
    "es": {
        "nav_guides": "Guías", "copy_title": "Copiar código", "lang_label": "Idioma",
        "foot_home": "Inicio", "foot_guides": "Todas las guías", "foot_claim": "Obtener el bono RINGO5", "foot_disclaimer": "Aviso legal",
        "tg": "Telegram", "phone": "Teléfono", "refcode": "Código de referido",
        "foot_p": "RinGoMining lo gestiona Ringo, embajador independiente de GoMining. No está afiliado a GoMining, no trabaja para GoMining ni habla en su nombre. Nada de esto es asesoramiento financiero. Las recompensas de minería no están garantizadas y puedes perder dinero.",
        "read_disclaimer": "Leer el aviso legal completo", "sticky": "Obtener mi bono RINGO5 →",
        "crumb_home": "Inicio", "crumb_guides": "Guías",
        "by": "Por Ringo", "by_rest": "10+ años en cripto · minando en GoMining desde mayo de 2025",
        "updated": "Actualizado el", "ambassador": "Embajador independiente de GoMining, no afiliado a GoMining. Los enlaces usan mi código de referido.",
        "faq_kicker": "Respuestas rápidas", "faq_h2": "Preguntas frecuentes", "keep_reading": "Sigue leyendo", "read": "Leer →",
        "hub_title": "Guías de GoMining: código promocional, opiniones, rentabilidad y más | RinGoMining",
        "hub_desc": "Respuestas directas sobre GoMining de un veterano con más de 10 años en cripto que mina en la plataforma a diario: el código promocional RINGO5, si es confiable, resultados reales, rentabilidad y cómo empezar.",
        "hub_eyebrow": "Respuestas directas · Código RINGO5", "hub_h1": "Todo lo que te contaría sobre GoMining",
        "hub_lede": "Llevo más de 10 años en cripto y mino en GoMining desde mayo de 2025. Estas son las preguntas que más me hacen, respondidas con mis números reales.",
        "hub_short": "Guías",
    },
    "de": {
        "nav_guides": "Ratgeber", "copy_title": "Code kopieren", "lang_label": "Sprache",
        "foot_home": "Start", "foot_guides": "Alle Ratgeber", "foot_claim": "RINGO5-Bonus sichern", "foot_disclaimer": "Haftungsausschluss",
        "tg": "Telegram", "phone": "Telefon", "refcode": "Empfehlungscode",
        "foot_p": "RinGoMining wird von Ringo betrieben, einem unabhängigen GoMining-Botschafter. Nicht mit GoMining verbunden, nicht bei GoMining angestellt und kein Sprecher von GoMining. Nichts hier ist Finanzberatung. Mining-Erträge sind nicht garantiert und du kannst Geld verlieren.",
        "read_disclaimer": "Vollständigen Haftungsausschluss lesen", "sticky": "Meinen RINGO5-Bonus sichern →",
        "crumb_home": "Start", "crumb_guides": "Ratgeber",
        "by": "Von Ringo", "by_rest": "10+ Jahre Krypto · mint auf GoMining seit Mai 2025",
        "updated": "Aktualisiert am", "ambassador": "Unabhängiger GoMining-Botschafter, nicht mit GoMining verbunden. Links enthalten meinen Empfehlungscode.",
        "faq_kicker": "Kurze Antworten", "faq_h2": "FAQ", "keep_reading": "Weiterlesen", "read": "Lesen →",
        "hub_title": "GoMining-Ratgeber: Promo-Code, Erfahrungen, Rentabilität & mehr | RinGoMining",
        "hub_desc": "Ehrliche Antworten zu GoMining von einem Krypto-Veteranen mit 10+ Jahren Erfahrung, der täglich darauf mint: der Promo-Code RINGO5, seriös oder Betrug, echte Ergebnisse, Rentabilität und der Einstieg.",
        "hub_eyebrow": "Klare Antworten · Code RINGO5", "hub_h1": "Alles, was ich dir über GoMining sagen würde",
        "hub_lede": "Ich bin seit über 10 Jahren im Krypto-Bereich und mine seit Mai 2025 auf GoMining. Das sind die Fragen, die mir am häufigsten gestellt werden, beantwortet mit meinen echten Zahlen.",
        "hub_short": "Ratgeber",
    },
}

APR = '<span data-gomining-rate>21.7%</span>'
APR_DATE = {
    "en": '<span data-gomining-rate="updated">Oct 7, 2026</span>',
    "fr": '<span data-gomining-rate="updated">7 oct. 2026</span>',
    "es": '<span data-gomining-rate="updated">7 oct 2026</span>',
    "de": '<span data-gomining-rate="updated">7. Okt. 2026</span>',
}

SRC_LINKS = {
    "usdc": '<a href="https://eco.com/support/en/articles/15182156-usdc-yield-2026" target="_blank" rel="noopener">Eco</a>',
    "sp": '<a href="https://www.fidelity.com/learning-center/trading-investing/sp-500-average-return" target="_blank" rel="noopener">Fidelity</a>',
}

VIDEOS = [
    ("mPJrgF6LXLk", "GoMining's HUGE Texas Mining Farm", {"en": "Texas", "fr": "Texas", "es": "Texas", "de": "Texas"}),
    ("Pd-QxvRNg9I", "West Texas Bitcoin Mining Farm Tour", {"en": "West Texas", "fr": "Ouest du Texas", "es": "Oeste de Texas", "de": "West-Texas"}),
    ("ukaVbFeufcU", "Mining Bitcoins in South Carolina", {"en": "South Carolina", "fr": "Caroline du Sud", "es": "Carolina del Sur", "de": "South Carolina"}),
    ("YOyW-LmCBnY", "Huge Mining Farm Tour", {"en": "South Carolina", "fr": "Caroline du Sud", "es": "Carolina del Sur", "de": "South Carolina"}),
]

# Text inside the reusable blocks. {P} = language prefix, {APR}/{APR_DATE} = live rate spans.
B = {
    "en": dict(
        hint_strong="Use code RINGO5 and the first terahash is on me.",
        hint_span="Plus 10% back, up to $1,000, and 5% back on every upgrade for life.",
        hint_btn="Claim my bonus →",
        offer_kicker="The RINGO5 bonus", offer_h2="Everyone's got a code. Here's why you'd use mine.",
        offer_p="It's my way of saying thanks. I really appreciate it when people use my code, so I reward them for it, paid by me, in crypto.",
        c1_big="$18.99", c1_h="The first terahash is on me", c1_p="You buy your first terahash, I send the $18.99 back.",
        c2_h="Cash back", c2_p="10% back on your purchases, up to $10,000. That's up to $1,000 in your wallet.",
        c3_h="Back for life", c3_p="5% back on every terahash upgrade you ever make.",
        c4_big="24h", c4_h="Fast payout", c4_p="USDC, Bitcoin, or GoMining tokens within 24 hours of confirming your purchase.",
        offer_fine="Putting in more than $10,000? Message me and I'll take care of you.", terms="Bonus terms",
        offer_btn="Lock in my RINGO5 bonus →",
        opt_h2="Run your own numbers",
        opt_p="Don't take my word for it. Type in what you'd put in, set the Bitcoin price you want to test, and the Optimizer splits it between terahash and GoMining tokens for the maximum discount, then projects your returns at today's rates.",
        opt_fine="Projections, not promises. Bitcoin's price, network difficulty, and rewards change every day. Not financial advice.",
        cmp_kicker="Where else would your money go?", cmp_h2="You know what your other money does. Here's what this does.",
        r_sav="Savings account", r_sav_v="Barely moves", r_usdc="USDC staking", r_usdc_v="~3–5% a year",
        r_sp="S&amp;P 500", r_sp_v="~10% a year, long-run average", r_home="Mining on your own machines",
        r_home_v="At average US power prices, often costs more than it earns", r_stake="GoMining token staking (veGOMINING)",
        r_stake_v="{APR} APR right now, <em>plus</em> your daily Bitcoin mining rewards",
        cmp_fine="Staking APR is live from my GMT Optimizer feed, updated {APR_DATE}. It moves; it's ranged roughly 22–25% over the past few months. Rates change. Not financial advice.",
        src_usdc="USDC yields across major platforms ran about 3–5% APY in October 2026 ({usdc}).",
        src_sp="The S&amp;P 500 has averaged about 10% a year since 1957 ({sp}).",
        hab_kicker="You don't need to be rich", hab_h2="$25 to $50 a week. That's not even hustling.",
        hab1_b="$25", hab1_s="a week", hab1_e="= $1,300 a year", hab2_b="$50", hab2_s="a week", hab2_e="= $2,600 a year",
        hab3_b="$13,000", hab3_s="in 5 years at $50/week", hab3_e="before it earns a thing",
        hab_p="And that's just the money you set aside. On GoMining it's mining Bitcoin and earning staking rewards the whole time, and those rewards can go right back in. If you're serious about building something, you can find $25 a week. Once you see the numbers, you'll probably want to do more.",
        hab_btn="See what that habit could grow into →",
        story_kicker="Who's writing this",
        story_p1="<strong>I'm Ringo.</strong> I've been in crypto for 10+ years. I held Bitcoin through the crash under $10k, bought XRP under 30 cents, and swapped over $1M of Shiba Inu in a single transaction. I took that money and built a real mining farm in Rochester, Michigan. When Bitcoin fell, the power bill ate the profits, so I shipped my machines to Toluca, Mexico for cheaper power. That facility burned down, and a technician there lost his life.",
        story_p2="That's why I mine on GoMining now: Bitcoin mining without owning machines that can break, burn, or hurt someone.", story_link="Read the full story →",
        st_kicker="Get your bonus in 3 steps", st_h2="Three minutes. Three steps.",
        st1_b="Tell me who you are", st1_s="A one-minute form so I can find you and pay you.",
        st2_b="Sign up with RINGO5", st2_s="GoMining opens in a new tab with my code attached. Buy your first terahash.",
        st3_b="Send me your GoMining ID", st3_s="I confirm it and pay your bonus within 24 hours.", st_btn="Start step 1 →",
        cl_h2="Stop waiting. Start mining.", cl_p="Nobody's coming to change your situation for you. The people who win are the ones who stop thinking about it and make a move.",
        cl_btn="Start with RINGO5 →",
        vid_play="Play", vid_fine="Videos by VoskCoin, an independent crypto YouTuber. Facility figures mentioned in them come from GoMining.",
    ),
    "fr": dict(
        hint_strong="Avec le code RINGO5, le premier térahash est pour moi.",
        hint_span="En plus : 10 % remboursés jusqu'à 1 000 $, et 5 % sur chaque amélioration, à vie.",
        hint_btn="Obtenir mon bonus →",
        offer_kicker="Le bonus RINGO5", offer_h2="Tout le monde a un code. Voici pourquoi utiliser le mien.",
        offer_p="C'est ma façon de dire merci. J'apprécie vraiment qu'on utilise mon code, alors je récompense ceux qui le font, payé par moi, en crypto.",
        c1_big="18,99 $", c1_h="Le premier térahash est pour moi", c1_p="Vous achetez votre premier térahash, je vous renvoie les 18,99 $.",
        c2_h="Cashback", c2_p="10 % remboursés sur vos achats, jusqu'à 10 000 $. Soit jusqu'à 1 000 $ dans votre portefeuille.",
        c3_h="À vie", c3_p="5 % remboursés sur chaque amélioration de térahash, pour toujours.",
        c4_big="24 h", c4_h="Paiement rapide", c4_p="En USDC, Bitcoin ou tokens GoMining, sous 24 heures après confirmation de votre achat.",
        offer_fine="Vous investissez plus de 10 000 $ ? Écrivez-moi, je m'occupe de vous.", terms="Conditions du bonus",
        offer_btn="Bloquer mon bonus RINGO5 →",
        opt_h2="Faites vos propres calculs",
        opt_p="Ne me croyez pas sur parole. Indiquez le montant que vous investiriez, choisissez le prix du Bitcoin à tester, et l'Optimizer répartit la somme entre térahash et tokens GoMining pour obtenir la remise maximale, puis projette vos gains aux taux actuels.",
        opt_fine="Des projections, pas des promesses. Le prix du Bitcoin, la difficulté du réseau et les récompenses changent chaque jour. Pas un conseil financier.",
        cmp_kicker="Où irait votre argent sinon ?", cmp_h2="Vous savez ce que rapporte le reste de votre argent. Voici ce que rapporte celui-ci.",
        r_sav="Livret / compte épargne", r_sav_v="Ça bouge à peine", r_usdc="Staking d'USDC", r_usdc_v="~3–5 % par an",
        r_sp="S&amp;P 500", r_sp_v="~10 % par an, moyenne historique", r_home="Miner avec ses propres machines",
        r_home_v="Au prix moyen de l'électricité aux États-Unis, ça coûte souvent plus que ça ne rapporte", r_stake="Staking du token GoMining (veGOMINING)",
        r_stake_v="{APR} d'APR en ce moment, <em>plus</em> vos récompenses de minage en Bitcoin chaque jour",
        cmp_fine="L'APR de staking vient en direct de mon flux GMT Optimizer, mis à jour le {APR_DATE}. Il varie ; il a oscillé entre 22 et 25 % environ ces derniers mois. Les taux changent. Pas un conseil financier.",
        src_usdc="Les rendements sur l'USDC tournaient autour de 3–5 % APY sur les grandes plateformes en octobre 2026 ({usdc}).",
        src_sp="Le S&amp;P 500 a rapporté environ 10 % par an en moyenne depuis 1957 ({sp}).",
        hab_kicker="Pas besoin d'être riche", hab_h2="25 à 50 $ par semaine. Ce n'est même pas un effort.",
        hab1_b="25 $", hab1_s="par semaine", hab1_e="= 1 300 $ par an", hab2_b="50 $", hab2_s="par semaine", hab2_e="= 2 600 $ par an",
        hab3_b="13 000 $", hab3_s="en 5 ans à 50 $/semaine", hab3_e="avant même de rapporter quoi que ce soit",
        hab_p="Et ce n'est que l'argent mis de côté. Sur GoMining, il mine du Bitcoin et génère des récompenses de staking pendant tout ce temps, et ces récompenses peuvent être réinvesties. Si vous voulez vraiment construire quelque chose, vous pouvez trouver 25 $ par semaine. Une fois les chiffres sous les yeux, vous voudrez sûrement faire plus.",
        hab_btn="Voir ce que cette habitude peut devenir →",
        story_kicker="Qui écrit ceci",
        story_p1="<strong>Je m'appelle Ringo.</strong> Je suis dans la crypto depuis plus de 10 ans. J'ai gardé mes Bitcoin pendant la chute sous les 10 000 $, acheté du XRP à moins de 30 centimes, et échangé plus d'un million de dollars de Shiba Inu en une seule transaction. Avec cet argent, j'ai monté une vraie ferme de minage à Rochester, dans le Michigan. Quand le Bitcoin a chuté, la facture d'électricité a mangé les profits, alors j'ai envoyé mes machines à Toluca, au Mexique, pour une électricité moins chère. Ce site a brûlé, et un technicien y a perdu la vie.",
        story_p2="C'est pour ça que je mine sur GoMining aujourd'hui : du minage de Bitcoin sans posséder de machines qui peuvent tomber en panne, brûler ou blesser quelqu'un.", story_link="Lire toute l'histoire →",
        st_kicker="Votre bonus en 3 étapes", st_h2="Trois minutes. Trois étapes.",
        st1_b="Dites-moi qui vous êtes", st1_s="Un formulaire d'une minute pour que je puisse vous retrouver et vous payer.",
        st2_b="Inscrivez-vous avec RINGO5", st2_s="GoMining s'ouvre dans un nouvel onglet avec mon code. Achetez votre premier térahash.",
        st3_b="Envoyez-moi votre ID GoMining", st3_s="Je le vérifie et je paie votre bonus sous 24 heures.", st_btn="Commencer l'étape 1 →",
        cl_h2="Arrêtez d'attendre. Commencez à miner.", cl_p="Personne ne viendra changer votre situation à votre place. Ceux qui gagnent sont ceux qui arrêtent d'y penser et passent à l'action.",
        cl_btn="Commencer avec RINGO5 →",
        vid_play="Lire", vid_fine="Vidéos de VoskCoin, YouTubeur crypto indépendant (en anglais). Les chiffres sur les installations cités viennent de GoMining.",
    ),
    "es": dict(
        hint_strong="Usa el código RINGO5 y el primer terahash corre por mi cuenta.",
        hint_span="Además, 10 % de vuelta hasta 1.000 $ y 5 % en cada mejora, de por vida.",
        hint_btn="Obtener mi bono →",
        offer_kicker="El bono RINGO5", offer_h2="Todos tienen un código. Por esto usarías el mío.",
        offer_p="Es mi manera de darte las gracias. De verdad valoro que la gente use mi código, así que lo recompenso, pagado por mí, en cripto.",
        c1_big="18,99 $", c1_h="El primer terahash corre por mi cuenta", c1_p="Compras tu primer terahash y yo te devuelvo los 18,99 $.",
        c2_h="Reembolso", c2_p="10 % de vuelta en tus compras, hasta 10.000 $. Hasta 1.000 $ en tu billetera.",
        c3_h="De por vida", c3_p="5 % de vuelta en cada mejora de terahash que hagas, siempre.",
        c4_big="24 h", c4_h="Pago rápido", c4_p="En USDC, Bitcoin o tokens GoMining, dentro de las 24 horas tras confirmar tu compra.",
        offer_fine="¿Vas a invertir más de 10.000 $? Escríbeme y me encargo de ti.", terms="Condiciones del bono",
        offer_btn="Asegurar mi bono RINGO5 →",
        opt_h2="Haz tus propios números",
        opt_p="No te fíes solo de mi palabra. Escribe cuánto invertirías, elige el precio de Bitcoin que quieras probar, y el Optimizer lo reparte entre terahash y tokens GoMining para conseguir el máximo descuento, y luego proyecta tus ganancias a las tasas de hoy.",
        opt_fine="Proyecciones, no promesas. El precio de Bitcoin, la dificultad de la red y las recompensas cambian cada día. No es asesoramiento financiero.",
        cmp_kicker="¿A dónde más iría tu dinero?", cmp_h2="Sabes lo que hace el resto de tu dinero. Esto es lo que hace este.",
        r_sav="Cuenta de ahorro", r_sav_v="Casi no se mueve", r_usdc="Staking de USDC", r_usdc_v="~3–5 % al año",
        r_sp="S&amp;P 500", r_sp_v="~10 % al año, promedio histórico", r_home="Minar con tus propias máquinas",
        r_home_v="Con el precio medio de la luz en EE. UU., a menudo cuesta más de lo que gana", r_stake="Staking del token GoMining (veGOMINING)",
        r_stake_v="{APR} de APR ahora mismo, <em>más</em> tus recompensas diarias de minería de Bitcoin",
        cmp_fine="El APR de staking viene en vivo de mi feed de GMT Optimizer, actualizado el {APR_DATE}. Varía; ha estado entre un 22 y un 25 % aprox. en los últimos meses. Las tasas cambian. No es asesoramiento financiero.",
        src_usdc="Los rendimientos de USDC en las grandes plataformas rondaban el 3–5 % APY en octubre de 2026 ({usdc}).",
        src_sp="El S&amp;P 500 ha rendido en promedio cerca de un 10 % anual desde 1957 ({sp}).",
        hab_kicker="No necesitas ser rico", hab_h2="25 a 50 $ por semana. Eso ni siquiera es esforzarse.",
        hab1_b="25 $", hab1_s="por semana", hab1_e="= 1.300 $ al año", hab2_b="50 $", hab2_s="por semana", hab2_e="= 2.600 $ al año",
        hab3_b="13.000 $", hab3_s="en 5 años a 50 $/semana", hab3_e="antes de ganar nada",
        hab_p="Y eso es solo el dinero que apartas. En GoMining está minando Bitcoin y generando recompensas de staking todo el tiempo, y esas recompensas se pueden reinvertir. Si vas en serio con construir algo, puedes encontrar 25 $ a la semana. Cuando veas los números, seguramente querrás hacer más.",
        hab_btn="Mira en qué podría convertirse ese hábito →",
        story_kicker="Quién escribe esto",
        story_p1="<strong>Soy Ringo.</strong> Llevo más de 10 años en cripto. Aguanté mis Bitcoin durante la caída por debajo de 10.000 $, compré XRP por menos de 30 centavos y cambié más de un millón de dólares en Shiba Inu en una sola transacción. Con ese dinero monté una granja de minería real en Rochester, Michigan. Cuando Bitcoin cayó, la factura de la luz se comió las ganancias, así que envié mis máquinas a Toluca, México, por una electricidad más barata. Esa instalación se incendió y un técnico perdió la vida.",
        story_p2="Por eso hoy mino en GoMining: minería de Bitcoin sin tener máquinas que se rompan, se quemen o lastimen a alguien.", story_link="Lee la historia completa →",
        st_kicker="Tu bono en 3 pasos", st_h2="Tres minutos. Tres pasos.",
        st1_b="Dime quién eres", st1_s="Un formulario de un minuto para poder encontrarte y pagarte.",
        st2_b="Regístrate con RINGO5", st2_s="GoMining se abre en una pestaña nueva con mi código. Compra tu primer terahash.",
        st3_b="Envíame tu ID de GoMining", st3_s="Lo confirmo y te pago el bono en 24 horas.", st_btn="Empezar el paso 1 →",
        cl_h2="Deja de esperar. Empieza a minar.", cl_p="Nadie va a venir a cambiar tu situación por ti. Los que ganan son los que dejan de pensarlo y se mueven.",
        cl_btn="Empezar con RINGO5 →",
        vid_play="Reproducir", vid_fine="Videos de VoskCoin, youtuber cripto independiente (en inglés). Las cifras de las instalaciones que se mencionan provienen de GoMining.",
    ),
    "de": dict(
        hint_strong="Mit dem Code RINGO5 geht der erste Terahash auf mich.",
        hint_span="Dazu 10 % zurück bis 1.000 $ und 5 % auf jedes Upgrade, lebenslang.",
        hint_btn="Bonus sichern →",
        offer_kicker="Der RINGO5-Bonus", offer_h2="Jeder hat einen Code. Darum solltest du meinen nehmen.",
        offer_p="Das ist mein Dankeschön. Ich weiß es wirklich zu schätzen, wenn jemand meinen Code nutzt, deshalb belohne ich das, bezahlt von mir, in Krypto.",
        c1_big="18,99 $", c1_h="Der erste Terahash geht auf mich", c1_p="Du kaufst deinen ersten Terahash, ich schicke dir die 18,99 $ zurück.",
        c2_h="Cashback", c2_p="10 % zurück auf deine Käufe bis 10.000 $. Also bis zu 1.000 $ in deiner Wallet.",
        c3_h="Lebenslang", c3_p="5 % zurück auf jedes Terahash-Upgrade, das du jemals machst.",
        c4_big="24 Std.", c4_h="Schnelle Auszahlung", c4_p="In USDC, Bitcoin oder GoMining-Token innerhalb von 24 Stunden nach Bestätigung deines Kaufs.",
        offer_fine="Du investierst mehr als 10.000 $? Schreib mir, ich kümmere mich um dich.", terms="Bonusbedingungen",
        offer_btn="RINGO5-Bonus sichern →",
        opt_h2="Rechne selbst nach",
        opt_p="Verlass dich nicht nur auf mein Wort. Gib ein, wie viel du investieren würdest, stell den Bitcoin-Preis ein, den du testen willst, und der Optimizer teilt den Betrag auf Terahash und GoMining-Token auf, für den maximalen Rabatt, und rechnet deine Erträge zu den heutigen Kursen hoch.",
        opt_fine="Prognosen, keine Versprechen. Bitcoin-Preis, Netzwerk-Difficulty und Erträge ändern sich täglich. Keine Finanzberatung.",
        cmp_kicker="Wo würde dein Geld sonst liegen?", cmp_h2="Du weißt, was dein übriges Geld macht. Das hier macht dieses.",
        r_sav="Sparkonto", r_sav_v="Bewegt sich kaum", r_usdc="USDC-Staking", r_usdc_v="~3–5 % pro Jahr",
        r_sp="S&amp;P 500", r_sp_v="~10 % pro Jahr im langjährigen Schnitt", r_home="Mining mit eigenen Geräten",
        r_home_v="Bei durchschnittlichen US-Strompreisen kostet es oft mehr, als es einbringt", r_stake="GoMining-Token-Staking (veGOMINING)",
        r_stake_v="{APR} APR aktuell, <em>plus</em> deine täglichen Bitcoin-Mining-Erträge",
        cmp_fine="Die Staking-APR kommt live aus meinem GMT-Optimizer-Feed, aktualisiert am {APR_DATE}. Sie schwankt; in den letzten Monaten lag sie etwa zwischen 22 und 25 %. Kurse ändern sich. Keine Finanzberatung.",
        src_usdc="USDC-Renditen lagen im Oktober 2026 auf großen Plattformen bei etwa 3–5 % APY ({usdc}).",
        src_sp="Der S&amp;P 500 brachte seit 1957 im Schnitt etwa 10 % pro Jahr ({sp}).",
        hab_kicker="Du musst nicht reich sein", hab_h2="25 bis 50 $ pro Woche. Das ist nicht mal Hustle.",
        hab1_b="25 $", hab1_s="pro Woche", hab1_e="= 1.300 $ pro Jahr", hab2_b="50 $", hab2_s="pro Woche", hab2_e="= 2.600 $ pro Jahr",
        hab3_b="13.000 $", hab3_s="in 5 Jahren bei 50 $/Woche", hab3_e="bevor es überhaupt etwas abwirft",
        hab_p="Und das ist nur das Geld, das du zurücklegst. Auf GoMining mint es die ganze Zeit Bitcoin und verdient Staking-Erträge, und die kannst du direkt reinvestieren. Wenn du es ernst meinst, findest du 25 $ pro Woche. Wenn du die Zahlen siehst, willst du wahrscheinlich mehr.",
        hab_btn="Sieh dir an, was aus dieser Gewohnheit werden kann →",
        story_kicker="Wer das hier schreibt",
        story_p1="<strong>Ich bin Ringo.</strong> Ich bin seit über 10 Jahren im Krypto-Bereich. Ich habe Bitcoin durch den Crash unter 10.000 $ gehalten, XRP unter 30 Cent gekauft und über 1 Mio. $ Shiba Inu in einer einzigen Transaktion getauscht. Mit dem Geld habe ich eine echte Mining-Farm in Rochester, Michigan aufgebaut. Als Bitcoin fiel, hat die Stromrechnung den Gewinn aufgefressen, also habe ich meine Geräte für günstigeren Strom nach Toluca, Mexiko geschickt. Diese Anlage ist abgebrannt, und ein Techniker hat dabei sein Leben verloren.",
        story_p2="Deshalb mine ich heute auf GoMining: Bitcoin-Mining, ohne Geräte zu besitzen, die kaputtgehen, abbrennen oder jemanden verletzen können.", story_link="Die ganze Geschichte lesen →",
        st_kicker="Dein Bonus in 3 Schritten", st_h2="Drei Minuten. Drei Schritte.",
        st1_b="Sag mir, wer du bist", st1_s="Ein Formular, eine Minute, damit ich dich finden und bezahlen kann.",
        st2_b="Mit RINGO5 registrieren", st2_s="GoMining öffnet sich in einem neuen Tab mit meinem Code. Kauf deinen ersten Terahash.",
        st3_b="Schick mir deine GoMining-ID", st3_s="Ich bestätige sie und zahle deinen Bonus innerhalb von 24 Stunden.", st_btn="Mit Schritt 1 starten →",
        cl_h2="Hör auf zu warten. Fang an zu minen.", cl_p="Niemand kommt und ändert deine Situation für dich. Gewinnen tun die, die aufhören zu grübeln und loslegen.",
        cl_btn="Mit RINGO5 starten →",
        vid_play="Abspielen", vid_fine="Videos von VoskCoin, einem unabhängigen Krypto-YouTuber (auf Englisch). Die genannten Zahlen zu den Anlagen stammen von GoMining.",
    ),
}
