// Locked one-page resume template. The LLM never touches this file.
// All content comes from data.json as plain strings (no markup injection).
#let data = json("data.json")

#set document(title: data.contact.name + " - Resume", author: data.contact.name)
#set page(paper: "us-letter", margin: (x: 0.5in, top: 0.45in, bottom: 0.45in))
#set text(font: "New Computer Modern", size: 10pt, lang: "en", hyphenate: false)
#set par(justify: false, leading: 0.48em, spacing: 0.48em)
#set list(indent: 0.4em, body-indent: 0.45em, spacing: 0.42em, marker: [•])

#let section(title) = block(
  width: 100%, above: 0.85em, below: 0.45em,
  stroke: (bottom: 0.5pt), inset: (bottom: 2.5pt),
  text(size: 10.5pt, weight: "bold", upper(title)),
)

#let entry(heading, dates, sub, loc, bullets) = {
  block(above: 0.62em, below: 0em, breakable: true)[
    #grid(
      columns: (1fr, auto),
      align: (left, right),
      row-gutter: 0.36em,
      text(weight: "bold", heading), text(dates),
      if sub != "" { emph(sub) }, if loc != "" { emph(loc) },
    )
    #if bullets.len() > 0 {
      v(0.08em)
      list(..bullets.map(b => [#b]))
    }
  ]
}

// ---- Header ----
#align(center)[
  #text(size: 17pt, weight: "bold", data.contact.name)
  #v(-0.35em)
  #let parts = (data.contact.location, data.contact.phone, link("mailto:" + data.contact.email, data.contact.email))
  #let parts = parts + data.contact.links.map(l => link(l.url, l.label))
  #text(size: 9.5pt, parts.map(p => box(p)).join([ #h(0.2em)|#h(0.2em) ]))
]

// ---- Education ----
#section("Education")
#for e in data.education {
  let dates = if e.start != "" { e.start + " – " + e.end } else { e.end }
  entry(e.school, dates, e.degree, e.location, e.details)
}

// ---- Experience ----
#section("Experience")
#for r in data.roles {
  entry(r.heading, r.dates, r.subheading, r.location, r.bullets)
}

// ---- Projects ----
#if data.projects.len() > 0 {
  section("Projects")
  for p in data.projects {
    entry(p.heading, p.dates, p.subheading, p.location, p.bullets)
  }
}

// ---- Skills ----
#section("Skills")
#for s in data.skills {
  block(above: 0.4em, below: 0em)[#text(weight: "bold", s.category + ": ")#s.items]
}

// Markers used by the fitter: how full the page is, and how many lines each
// bullet wraps to (width / available width, in document order).
#context [#metadata(here().position()) <end>]
#context {
  let avail = page.width - 1in - 0.85 * text.size - measure([•]).width
  let ratios = ()
  for grp in (data.roles, data.projects) {
    for e in grp {
      for b in e.bullets { ratios.push(measure([#b]).width / avail) }
    }
  }
  [#metadata(ratios) <widths>]
}
