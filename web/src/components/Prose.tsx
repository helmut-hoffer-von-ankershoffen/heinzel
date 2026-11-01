// Markdown as the app shows it, GitHub-flavoured (remark-gfm): headings, emphasis, strikethrough, inline and fenced
// code, quotes, nested and task lists, tables, rules, links and line breaks, in the theme's tokens. The chat's answers,
// the changelog and help all use it. No raw HTML is rendered; links go only to http(s), mailto or a screen of the app;
// images are never loaded (the CSP and your privacy), they show as a link with their alt text, as a terminal would.
import { Check, Copy, SquareTerminal } from "lucide-react";
import { type ComponentProps, isValidElement, type ReactNode, useState } from "react";
import Markdown, { type Components } from "react-markdown";
import { Link } from "react-router";
import remarkGfm from "remark-gfm";
import { runCommand } from "@/components/CommandActions";
import { t } from "@/i18n";
import { appCallFor } from "@/lib/operationCalls";
import { cn } from "@/lib/utils";

const OUTSIDE = /^(https?:|mailto:)/i;
const SHELLS = new Set(["sh", "bash", "shell", "zsh", "console", "terminal"]);

/** The text of a rendered node tree, e.g. a code block's children. */
function textOf(node: ReactNode): string {
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(textOf).join("");
  if (isValidElement<{ children?: ReactNode }>(node)) return textOf(node.props.children);
  return "";
}

/** A fenced code block: monospace, scrolling sideways, with Copy and, for a one-line shell command, Run. */
export function CodeBlock({ code, language }: { code: string; language: string }) {
  const [copied, setCopied] = useState(false);
  const command = code.trim().replace(/^\$ /, "");
  const runnable = SHELLS.has(language) && command !== "" && !command.includes("\n");
  const copy = async () => {
    await navigator.clipboard.writeText(code);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1500);
  };
  const button = "flex size-7 items-center justify-center rounded-md text-icon hover:bg-accent [&_svg]:size-3.5";
  return (
    <div className="group/code relative my-2 rounded-lg border border-border bg-muted">
      <pre className="overflow-x-auto p-3 pr-16 font-mono text-ui-sm leading-relaxed">
        <code>{code.replace(/\n$/, "")}</code>
      </pre>
      <div className="absolute top-1.5 right-1.5 flex gap-0.5">
        {runnable && (
          <button
            type="button"
            onClick={() => runCommand(command)}
            aria-label={appCallFor(command) ? t("run.hereAria", { command }) : t("run.aria", { command })}
            title={appCallFor(command) ? t("run.here") : t("run.label")}
            className={button}
          >
            <SquareTerminal aria-hidden />
          </button>
        )}
        <button
          type="button"
          onClick={() => void copy()}
          aria-label={t("prose.copyCode")}
          title={copied ? t("copy.done") : t("copy.label")}
          className={button}
        >
          {copied ? <Check aria-hidden /> : <Copy aria-hidden />}
        </button>
      </div>
    </div>
  );
}

const components: Components = {
  a: ({ href = "", children }) =>
    OUTSIDE.test(href) ? (
      <a href={href} target="_blank" rel="noreferrer noopener" className="text-primary underline">
        {children}
      </a>
    ) : href.startsWith("/") && !href.startsWith("//") ? (
      <Link to={href} className="text-primary underline">
        {children}
      </Link>
    ) : (
      <span>{children}</span>
    ),
  img: ({ src, alt }) => {
    const label = t("prose.image", { alt: alt || t("prose.untitled") });
    return typeof src === "string" && OUTSIDE.test(src) ? (
      <a href={src} target="_blank" rel="noreferrer noopener" className="text-primary underline">
        {label}
      </a>
    ) : (
      <span>{label}</span>
    );
  },
  pre: ({ children }) => {
    const child = Array.isArray(children) ? children[0] : children;
    const className = isValidElement<{ className?: string }>(child) ? (child.props.className ?? "") : "";
    const language = /language-([\w-]+)/.exec(className)?.[1]?.toLowerCase() ?? "";
    return <CodeBlock code={textOf(children)} language={language} />;
  },
  table: ({ children }) => (
    <div className="my-2 max-w-full overflow-x-auto">
      <table className="border-collapse text-ui-sm">{children}</table>
    </div>
  ),
  input: (props: ComponentProps<"input">) => <input {...props} disabled className="mr-1.5 align-middle" />,
};

const STYLE =
  "min-w-0 break-words leading-relaxed [&_blockquote]:my-2 [&_blockquote]:border-l-2 [&_blockquote]:border-border [&_blockquote]:pl-3 [&_blockquote]:text-muted-foreground [&_del]:text-muted-foreground [&_h1]:mt-3 [&_h1]:mb-2 [&_h1]:text-xl [&_h1]:font-semibold [&_h2]:mt-3 [&_h2]:mb-1.5 [&_h2]:text-lg [&_h2]:font-semibold [&_h3]:mt-2 [&_h3]:mb-1 [&_h3]:font-medium [&_h4]:font-medium [&_hr]:my-3 [&_hr]:border-border [&_li]:my-0.5 [&_ol]:list-decimal [&_ol]:pl-5 [&_p]:my-1.5 [&_strong]:font-semibold [&_td]:border [&_td]:border-border [&_td]:px-2 [&_td]:py-1 [&_th]:border [&_th]:border-border [&_th]:bg-muted [&_th]:px-2 [&_th]:py-1 [&_th]:text-left [&_ul]:list-disc [&_ul]:pl-5 [&_li>ul]:my-0 [&_li>ol]:my-0 [&_.contains-task-list]:list-none [&_.contains-task-list]:pl-1 [&_:not(pre)>code]:rounded [&_:not(pre)>code]:bg-muted [&_:not(pre)>code]:px-1 [&_:not(pre)>code]:font-mono [&_:not(pre)>code]:text-[0.9em]";

/** `text` as Markdown; an unfinished one (an answer still streaming) renders as far as it goes. */
export function Prose({ children, className }: { children: string; className?: string }) {
  return (
    <div className={cn(STYLE, className)}>
      <Markdown remarkPlugins={[remarkGfm]} components={components} skipHtml>
        {children}
      </Markdown>
    </div>
  );
}
