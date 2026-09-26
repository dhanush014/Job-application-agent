// Cover letter template: same header as the resume so the two look like a set.
// Content comes from sys.inputs.data (JSON) as plain strings.
#let data = json(bytes(sys.inputs.data))

#set document(title: data.contact.name + " - Cover Letter", author: data.contact.name)
#set page(paper: "us-letter", margin: (x: 0.9in, top: 0.7in, bottom: 0.8in))
#set text(font: "New Computer Modern", size: 11pt, lang: "en", hyphenate: false)
#set par(justify: false, leading: 0.62em, spacing: 1.15em)

#align(center)[
  #text(size: 18pt, weight: "bold", data.contact.name)
  #v(-0.35em)
  #let parts = (data.contact.location, data.contact.phone, link("mailto:" + data.contact.email, data.contact.email))
  #let parts = parts + data.contact.links.map(l => link(l.url, l.label))
  #let line-of(size) = text(size: size, parts.map(p => box(p)).join([ #h(0.2em)|#h(0.2em) ]))
  // shrink the contact line until it fits on one line
  #context {
    let w = measure(line-of(9.5pt)).width
    let avail = page.width - 1.8in
    line-of(if w > avail { 9.5pt * (avail / w) * 0.98 } else { 9.5pt })
  }
]
#v(-0.4em)
#line(length: 100%, stroke: 0.5pt)
#v(0.6em)

#data.date

#text(weight: "bold", "Re: " + data.title + " at " + data.company)

// each paragraph is a list of lines (keeps "Sincerely,\nName" on two lines)
#for p in data.paragraphs [#p.join(linebreak()) #parbreak()]
