// Locked one-page resume template. The LLM never touches this file.
// All content comes from sys.inputs.data (JSON) as plain strings (no markup injection).
//
// data.layout.font (0..1) sets the type size (9.5pt -> 11pt); data.layout.spacing
// (0..1) opens up the gaps between bullets, entries and sections. The fitter
// uses both to fill the page, and data.layout.stretch spreads any last sliver
// of leftover space between sections.
#let data = json(bytes(sys.inputs.data))
#let f = data.layout.font
#let sp = data.layout.spacing
#let fs = (9.5 + 1.5 * f) * 1pt
#let k = 0.8 + 0.6 * f  // base spacing multiplier; 1.0 at the 10pt baseline
#let lead = k * (1 + 0.3 * sp)   // line leading inside a bullet
#let item = k * (1 + 0.9 * sp)   // between bullets
#let gapk = k * (1 + 1.4 * sp)   // between entries and around section headings

#set document(title: data.contact.name + " - Resume", author: data.contact.name)
#set page(paper: "us-letter", margin: (x: 0.5in, top: 0.45in, bottom: 0.45in))
#set text(font: "New Computer Modern", size: fs, lang: "en", hyphenate: false)
#set par(justify: false, leading: 0.48em * lead, spacing: 0.48em * item)
#set list(indent: 0.4em, body-indent: 0.45em, spacing: 0.42em * item, marker: [•])

#let gap() = if data.layout.stretch { v(1fr) }

#let section(title) = block(
  width: 100%, above: 0.85em * gapk, below: 0.45em * gapk,
  stroke: (bottom: 0.5pt), inset: (bottom: 2.5pt),
  text(size: fs + 0.5pt, weight: "bold", upper(title)),
)

#let entry(heading, dates, sub, loc, bullets) = {
  block(above: 0.62em * gapk, below: 0em, breakable: true)[
    #grid(
      columns: (1fr, auto),
      align: (left, right),
      row-gutter: 0.36em * k,
      text(weight: "bold", heading), text(dates),
      if sub != "" { emph(sub) }, if loc != "" { emph(loc) },
    )
    #if bullets.len() > 0 {
      v(0.08em * k)
      list(..bullets)
    }
  ]
}

// Bullet text with its skill keywords in bold (runs come pre-split from Python).
#let rich(e) = range(e.bullets.len()).map(i => {
  if e.segments.len() > i {
    e.segments.at(i).map(x => if x.b { strong(x.t) } else { text(x.t) }).join()
  } else { [#e.bullets.at(i)] }
})

#let contact-line = {
  let parts = (data.contact.location, data.contact.phone, link("mailto:" + data.contact.email, data.contact.email))
  parts = parts + data.contact.links.map(l => link(l.url, l.label))
  text(size: fs - 0.5pt, parts.map(p => box(p)).join([ #h(0.2em)|#h(0.2em) ]))
}
#let skill-line(sk) = [#text(weight: "bold", sk.category + ": ")#sk.items]

// ---- Header ----
#align(center)[
  #text(size: fs * 1.7, weight: "bold", data.contact.name)
  #v(-0.35em)
  #contact-line
]

// ---- Education ----
#section("Education")
#for e in data.education {
  let dates = if e.start != "" { e.start + " – " + e.end } else { e.end }
  entry(e.school, dates, e.degree, e.location, e.details.map(d => [#d]))
}

// ---- Experience ----
#gap()
#section("Experience")
#for r in data.roles {
  entry(r.heading, r.dates, r.subheading, r.location, rich(r))
}

// ---- Projects ----
#if data.projects.len() > 0 {
  gap()
  section("Projects")
  for p in data.projects {
    entry(p.heading, p.dates, p.subheading, p.location, rich(p))
  }
}

// ---- Skills ----
#gap()
#section("Skills")
#for sk in data.skills {
  block(above: 0.4em * item, below: 0em, skill-line(sk))
}

// Markers used by the fitter: where the content ends, and how wide each line
// is relative to the space available (>1 means it wraps).
#context [#metadata(here().position()) <end>]
#context {
  let full = page.width - 1in
  let avail = full - 0.85 * text.size - measure([•]).width
  let bullets = ()
  for grp in (data.roles, data.projects) {
    for e in grp {
      for b in rich(e) { bullets.push(measure(b).width / avail) }
    }
  }
  [#metadata((
    bullets: bullets,
    header: measure(contact-line).width / full,
    skills: data.skills.map(sk => measure(skill-line(sk)).width / full),
  )) <widths>]
}
