"use client";
import { memo, useMemo } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

export interface Heading { level: number; text: string; slug: string; line: number }

const strip = (s: string) =>
  s.replace(/!?\[([^\]]*)\]\([^)]*\)/g, "$1").replace(/[`*_~]/g, "").replace(/<[^>]+>/g, "").trim();

/** Extract ATX headings (skipping fenced code) with unique slugs; keyed by 1-based line. */
export function extractHeadings(md: string): Heading[] {
  const out: Heading[] = [];
  const seen = new Map<string, number>();
  let fence = "";
  md.split("\n").forEach((raw, i) => {
    const line = raw.trimStart();
    const f = line.match(/^(```+|~~~+)/);
    if (f) {
      if (!fence) fence = f[1][0];
      else if (f[1][0] === fence) fence = "";
      return;
    }
    if (fence) return;
    const m = raw.match(/^ {0,3}(#{1,6})\s+(.+?)\s*#*\s*$/);
    if (!m) return;
    const text = strip(m[2]);
    const base = text.toLowerCase().replace(/[^\p{L}\p{N}\s-]/gu, "").trim().replace(/\s+/g, "-") || "section";
    const n = seen.get(base) ?? 0;
    seen.set(base, n + 1);
    out.push({ level: m[1].length, text, slug: n ? `${base}-${n}` : base, line: i + 1 });
  });
  return out;
}

/** Safe Markdown renderer: no raw HTML (react-markdown default), external links hardened. */
export const Markdown = memo(function Markdown({
  children, className = "", headingIds = false,
}: { children: string; className?: string; headingIds?: boolean }) {
  const comps = useMemo<Components>(() => {
    const byLine = new Map<number, string>();
    if (headingIds) for (const h of extractHeadings(children)) byLine.set(h.line, h.slug);
    const H = (Tag: "h1" | "h2" | "h3" | "h4" | "h5" | "h6"): Components[typeof Tag] =>
      function Heading({ node, children: c }) {
        const id = headingIds ? byLine.get(node?.position?.start.line ?? -1) : undefined;
        return <Tag id={id} className="scroll-mt-20">{c}</Tag>;
      };
    return {
      h1: H("h1"), h2: H("h2"), h3: H("h3"), h4: H("h4"), h5: H("h5"), h6: H("h6"),
      a: ({ href, children: c }) => {
        const safe = href && /^(https?:|mailto:|#|\/)/i.test(href) ? href : undefined;
        return <a href={safe} target={safe?.startsWith("#") ? undefined : "_blank"} rel="noreferrer noopener">{c}</a>;
      },
      img: ({ alt }) => <span className="text-muted">[image{alt ? `: ${alt}` : ""}]</span>,
      table: ({ children: c }) => <div className="table-wrap"><table>{c}</table></div>,
    };
  }, [children, headingIds]);

  return (
    <div className={`prose-md ${className}`}>
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={comps} skipHtml>
        {children}
      </ReactMarkdown>
    </div>
  );
});
