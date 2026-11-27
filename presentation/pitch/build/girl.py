import sys
def svg(mouth="smile", eyes="open"):
    O="#19161d"; SK="#ffe4d6"; SKS="#f6c7b3"; HR="#3a2730"; HR2="#5a3b45"
    eye_open = lambda cx: f'''
      <g>
        <ellipse cx="{cx}" cy="318" rx="34" ry="42" fill="#fff"/>
        <ellipse cx="{cx}" cy="324" rx="27" ry="35" fill="url(#iris)"/>
        <ellipse cx="{cx}" cy="330" rx="13" ry="17" fill="#2a1a20"/>
        <ellipse cx="{cx-9}" cy="308" rx="9" ry="11" fill="#fff"/>
        <circle cx="{cx+10}" cy="340" r="4.5" fill="#fff" opacity=".9"/>
        <path d="M{cx-40},300 Q{cx},266 {cx+40},296" fill="none" stroke="{O}" stroke-width="9" stroke-linecap="round"/>
        <path d="M{cx+36},293 l12,-8" stroke="{O}" stroke-width="6" stroke-linecap="round"/>
        <path d="M{cx-24},362 Q{cx},368 {cx+22},360" fill="none" stroke="{O}" stroke-width="3" stroke-linecap="round" opacity=".7"/>
      </g>'''
    eye_closed = lambda cx: f'<path d="M{cx-36},322 Q{cx},346 {cx+36},322" fill="none" stroke="{O}" stroke-width="8" stroke-linecap="round"/><path d="M{cx+32},320 l12,-6" stroke="{O}" stroke-width="5" stroke-linecap="round"/>'
    E = eye_open if eyes=="open" else eye_closed
    mouths = {
      "smile": f'<path d="M282,412 Q300,428 318,410" fill="none" stroke="{O}" stroke-width="5" stroke-linecap="round"/>',
      "small": f'<path d="M285,408 Q300,404 315,408 Q312,428 300,429 Q288,428 285,408 Z" fill="#7a2d3a" stroke="{O}" stroke-width="4" stroke-linejoin="round"/><path d="M291,421 Q300,416 309,421 Q305,428 300,428 Q295,428 291,421Z" fill="#ff8f9e"/>',
      "open":  f'<path d="M280,404 Q300,398 320,404 Q317,442 300,444 Q283,442 280,404 Z" fill="#7a2d3a" stroke="{O}" stroke-width="4" stroke-linejoin="round"/><path d="M286,428 Q300,418 314,428 Q309,442 300,442 Q291,442 286,428Z" fill="#ff8f9e"/><path d="M284,405 Q300,401 316,405 L314,411 Q300,407 286,411Z" fill="#fff"/>',
    }
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 600 760" width="600" height="760">
  <defs>
    <radialGradient id="iris" cx="50%" cy="65%" r="60%"><stop offset="0" stop-color="#ffb35c"/><stop offset=".55" stop-color="#c8662a"/><stop offset="1" stop-color="#5a2a1a"/></radialGradient>
    <linearGradient id="hair" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{HR2}"/><stop offset="1" stop-color="{HR}"/></linearGradient>
    <linearGradient id="blz" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#5248d6"/><stop offset="1" stop-color="#3a31b0"/></linearGradient>
  </defs>
  <!-- back hair -->
  <path d="M140,300 C130,160 220,90 300,90 C390,90 472,160 460,300 C470,420 470,500 440,560 L160,560 C130,500 130,420 140,300 Z" fill="url(#hair)" stroke="{O}" stroke-width="6"/>
  <!-- body -->
  <path d="M90,760 C95,640 170,585 300,585 C430,585 505,640 510,760 Z" fill="url(#blz)" stroke="{O}" stroke-width="6"/>
  <path d="M235,590 L300,690 L365,590 Q300,575 235,590 Z" fill="#fff8e9" stroke="{O}" stroke-width="5" stroke-linejoin="round"/>
  <path d="M235,590 L268,640 L222,662 L200,610 Z" fill="#4a40c8" stroke="{O}" stroke-width="5" stroke-linejoin="round"/>
  <path d="M365,590 L332,640 L378,662 L400,610 Z" fill="#4a40c8" stroke="{O}" stroke-width="5" stroke-linejoin="round"/>
  <circle cx="300" cy="712" r="7" fill="#ffe148" stroke="{O}" stroke-width="3"/>
  <!-- neck -->
  <path d="M262,500 L262,592 Q300,612 338,592 L338,500 Z" fill="{SKS}" stroke="{O}" stroke-width="5"/>
  <!-- face -->
  <path d="M168,300 C168,200 228,160 300,160 C372,160 432,200 432,300 C432,380 398,446 344,478 Q300,500 256,478 C202,446 168,380 168,300 Z" fill="{SK}" stroke="{O}" stroke-width="6"/>
  <ellipse cx="225" cy="385" rx="26" ry="13" fill="#ff8f9e" opacity=".45"/>
  <ellipse cx="375" cy="385" rx="26" ry="13" fill="#ff8f9e" opacity=".45"/>
  {E(236)}{E(364)}
  <path d="M206,262 Q236,248 264,258" fill="none" stroke="{HR}" stroke-width="6" stroke-linecap="round"/>
  <path d="M336,258 Q364,248 394,262" fill="none" stroke="{HR}" stroke-width="6" stroke-linecap="round"/>
  <path d="M300,372 l-5,14 l8,0" fill="none" stroke="#d99a86" stroke-width="3.5" stroke-linecap="round" stroke-linejoin="round"/>
  {mouths[mouth]}
  <!-- bangs -->
  <path d="M150,300 C140,170 230,96 304,98 C392,100 462,170 452,300 C440,250 420,226 400,214 L392,262 L362,206 L342,256 L318,200 L290,252 L266,204 L248,262 L222,214 C190,232 162,262 150,300 Z" fill="url(#hair)" stroke="{O}" stroke-width="6" stroke-linejoin="round"/>
  <path d="M232,140 Q280,118 330,128" fill="none" stroke="#7a5560" stroke-width="7" stroke-linecap="round" opacity=".7"/>
  <!-- side locks -->
  <path d="M168,300 C160,360 162,430 178,470 L196,456 C186,410 186,350 190,300 Z" fill="url(#hair)" stroke="{O}" stroke-width="5" stroke-linejoin="round"/>
  <path d="M432,300 C440,360 438,430 422,470 L404,456 C414,410 414,350 410,300 Z" fill="url(#hair)" stroke="{O}" stroke-width="5" stroke-linejoin="round"/>
  <!-- star clip -->
  <path d="M398,176 l9,18 20,3 -14,14 4,20 -19,-9 -18,10 3,-20 -15,-14 20,-3 Z" fill="#ff612f" stroke="{O}" stroke-width="4" stroke-linejoin="round"/>
</svg>'''
out=sys.argv[1]
for m in ["smile","small","open"]:
    for e in ["open","closed"]:
        open(f"{out}/girl-{m}-{e}.svg","w").write(svg(m,e))
