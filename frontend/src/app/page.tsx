"use client";

import { useState } from "react";
import Link from "next/link";

type IconName = "archive" | "arrow" | "book" | "check" | "chevron" | "clock" | "command" | "file" | "grid" | "lock" | "more" | "plus" | "search" | "shield" | "users";

function Icon({ name, className = "" }: { name: IconName; className?: string }) {
  const icons: Record<IconName, React.ReactNode> = {
    archive: <><path d="M3 7.5h18" /><path d="M5 7.5V19a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V7.5" /><path d="M4 3h16v4.5H4z" /><path d="M9 12h6" /></>,
    arrow: <><path d="M5 12h14" /><path d="m13 6 6 6-6 6" /></>,
    book: <><path d="M4.5 5.5A2.5 2.5 0 0 1 7 3h12.5v16H7a2.5 2.5 0 0 0-2.5 2.5z" /><path d="M4.5 5.5v15" /><path d="M8 7h8" /></>,
    check: <path d="m5 12 4.2 4L19 6" />,
    chevron: <path d="m9 18 6-6-6-6" />,
    clock: <><circle cx="12" cy="12" r="8.5" /><path d="M12 7v5l3.5 2" /></>,
    command: <path d="M9 9V6a3 3 0 1 0-3 3h12a3 3 0 1 0-3-3v12a3 3 0 1 0 3-3H6a3 3 0 1 0 3 3V6" />,
    file: <><path d="M7 3h7l4 4v14H7a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2Z" /><path d="M14 3v5h5" /><path d="M8.5 13h7" /><path d="M8.5 16.5h5" /></>,
    grid: <><rect x="4" y="4" width="6" height="6" rx="1" /><rect x="14" y="4" width="6" height="6" rx="1" /><rect x="4" y="14" width="6" height="6" rx="1" /><rect x="14" y="14" width="6" height="6" rx="1" /></>,
    lock: <><rect x="5" y="10" width="14" height="11" rx="2" /><path d="M8 10V7a4 4 0 0 1 8 0v3" /><path d="M12 14v3" /></>,
    more: <><circle cx="5" cy="12" r="1" fill="currentColor" stroke="none" /><circle cx="12" cy="12" r="1" fill="currentColor" stroke="none" /><circle cx="19" cy="12" r="1" fill="currentColor" stroke="none" /></>,
    plus: <><path d="M12 5v14" /><path d="M5 12h14" /></>,
    search: <><circle cx="10.75" cy="10.75" r="6.75" /><path d="m16 16 4 4" /></>,
    shield: <><path d="M12 3.5 19 6v5c0 4.6-2.9 8.3-7 9.5-4.1-1.2-7-4.9-7-9.5V6z" /><path d="m9 12 2 2 4-4" /></>,
    users: <><path d="M16 20v-1.5a4 4 0 0 0-4-4H7a4 4 0 0 0-4 4V20" /><circle cx="9.5" cy="7" r="3.5" /><path d="M17 11a3.5 3.5 0 0 0 0-7" /><path d="M21 20v-1.5a4 4 0 0 0-3-3.87" /></>,
  };
  return <svg aria-hidden="true" className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">{icons[name]}</svg>;
}

const documents = [
  { name: "Employee handbook", type: "PDF", size: "2.4 MB", state: "Indexed", tone: "ready" },
  { name: "Q3 operating plan", type: "PDF", size: "4.8 MB", state: "Indexing", tone: "working" },
  { name: "Security playbook", type: "DOCX", size: "1.2 MB", state: "Indexed", tone: "ready" },
  { name: "Engineering RFCs", type: "Collection", size: "18 files", state: "Indexed", tone: "ready" },
];

function Mark() {
  return <span className="grid size-8 place-items-center rounded-[7px] bg-[#d7ee66] text-[#171a0d]"><Icon name="command" className="size-4.5" /></span>;
}

function SignIn({ onEnter }: { onEnter: () => void }) {
  const [method, setMethod] = useState<"start" | "email">("start");
  return (
    <main className="grid min-h-screen bg-[#0b0c0d] lg:grid-cols-[1.05fr_0.95fr]">
      <section className="flex min-h-[48vh] flex-col border-b border-white/[0.08] px-6 py-6 lg:min-h-screen lg:border-b-0 lg:border-r lg:px-10 xl:px-14">
        <Link href="/" className="flex items-center gap-2.5 text-[15px] font-semibold tracking-[-0.03em]"><Mark /> Northstar</Link>
        <div className="my-auto max-w-[520px] py-16 lg:py-0">
          <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-[#9da276]">Knowledge, with boundaries</p>
          <h1 className="mt-4 text-[38px] font-medium leading-[1.04] tracking-[-0.055em] text-[#f0f0eb] sm:text-[52px]">Your team&apos;s work, searchable by the right people.</h1>
          <p className="mt-6 max-w-[430px] text-[15px] leading-7 text-[#969892]">Northstar indexes the documents your teams need while enforcing tenant, department, role, and individual access at retrieval time.</p>
          <div className="mt-10 space-y-4 border-t border-white/[0.08] pt-7">
            {["Tenant-isolated document storage", "Department and role-aware retrieval", "Grounded answers with source citations"].map((item) => <div key={item} className="flex items-center gap-3 text-[13px] text-[#d2d3cd]"><span className="grid size-5 place-items-center rounded-full border border-[#a9d65e]/30 text-[#a9d65e]"><Icon name="check" className="size-3" /></span>{item}</div>)}
          </div>
        </div>
        <p className="text-[11px] text-[#686a65]">© 2026 Northstar Systems</p>
      </section>

      <section className="flex items-center justify-center px-6 py-12 sm:px-10">
        <div className="w-full max-w-[376px]">
          <p className="text-[11px] font-semibold uppercase tracking-[0.15em] text-[#84867f]">Secure sign in</p>
          <h2 className="mt-3 text-[27px] font-medium tracking-[-0.045em] text-[#efefe9]">Welcome back</h2>
          <p className="mt-2 text-[13px] leading-6 text-[#858780]">Use your organization account to continue.</p>
          {method === "start" ? <>
            <button onClick={onEnter} className="mt-8 flex h-11 w-full items-center justify-center gap-2 rounded-md bg-[#e9e9e3] text-[13px] font-semibold text-[#171817] transition-colors hover:bg-white"><span className="grid size-4 place-items-center rounded-full bg-[conic-gradient(#4285f4_0_25%,#34a853_0_50%,#fbbc05_0_75%,#ea4335_0_100%)] text-[0]" />Continue with Google</button>
            <div className="my-6 flex items-center gap-3 text-[10px] uppercase tracking-[0.14em] text-[#61635f]"><span className="h-px flex-1 bg-white/[0.09]" />or<span className="h-px flex-1 bg-white/[0.09]" /></div>
            <button onClick={() => setMethod("email")} className="flex h-11 w-full items-center justify-center gap-2 rounded-md border border-white/[0.12] text-[13px] font-medium text-[#dedfd9] transition-colors hover:bg-white/[0.06]"><Icon name="lock" className="size-4 text-[#adb095]" />Continue with email</button>
          </> : <form onSubmit={(event) => { event.preventDefault(); onEnter(); }} className="mt-8">
            <label htmlFor="email" className="text-[12px] font-medium text-[#d5d6d0]">Work email</label>
            <input id="email" type="email" autoComplete="email" placeholder="you@company.com" required className="mt-2 h-11 w-full rounded-md border border-white/[0.12] bg-white/[0.035] px-3 text-[13px] text-white outline-none placeholder:text-[#656761] focus:border-[#c6dc5a]" />
            <button className="mt-4 flex h-11 w-full items-center justify-center rounded-md bg-[#d7ee66] text-[13px] font-semibold text-[#20220f] transition-colors hover:bg-[#e2f57b]">Continue with email</button>
            <button type="button" onClick={() => setMethod("start")} className="mt-4 w-full text-[12px] text-[#999b94] hover:text-white">Use another method</button>
          </form>}
          <p className="mt-7 flex items-center gap-2 text-[11px] leading-5 text-[#6e706b]"><Icon name="shield" className="size-4 shrink-0 text-[#8c9771]" />Authentication is managed by your organization&apos;s configured identity provider.</p>
        </div>
      </section>
    </main>
  );
}

function Dashboard({ onSignOut }: { onSignOut: () => void }) {
  const nav = [["Overview", "grid"], ["Documents", "file"], ["Collections", "archive"], ["People & access", "users"]] as const;
  return <div className="min-h-screen bg-[#0b0c0d] text-[#e9e9e5] selection:bg-[#d7ee66] selection:text-[#10110b]"><div className="mx-auto flex min-h-screen max-w-[1720px]">
    <aside className="hidden w-[244px] shrink-0 flex-col border-r border-white/[0.08] bg-[#101112] px-4 py-5 lg:flex">
      <a href="#overview" className="flex items-center gap-2.5 text-[15px] font-semibold tracking-[-0.03em]"><Mark /> Northstar</a>
      <div className="mt-9 px-2.5"><p className="text-[10px] font-semibold uppercase tracking-[0.14em] text-[#737570]">Workspace</p><button className="mt-2 flex w-full items-center justify-between rounded-md border border-white/[0.09] bg-white/[0.035] px-2.5 py-2 text-left text-[13px] text-[#d8d9d3]"><span className="truncate">Acme Corporation</span><Icon name="chevron" className="size-3.5 text-[#868883]" /></button></div>
      <nav className="mt-7 space-y-1" aria-label="Primary navigation">{nav.map(([label, icon], index) => <a key={label} href={`#${label.toLowerCase().replaceAll(" ", "-").replaceAll("&", "and")}`} className={`flex items-center gap-3 rounded-md px-2.5 py-2 text-[13px] transition-colors ${index === 0 ? "bg-white/[0.085] text-white" : "text-[#969892] hover:bg-white/[0.045] hover:text-[#e5e6e0]"}`}><Icon name={icon} className="size-4" />{label}</a>)}</nav>
      <div className="mt-auto border-t border-white/[0.08] pt-4"><a href="#documentation" className="flex items-center gap-3 rounded-md px-2.5 py-2 text-[13px] text-[#969892] hover:bg-white/[0.045] hover:text-[#e5e6e0]"><Icon name="book" className="size-4" />Documentation</a><button onClick={onSignOut} className="mt-3 flex w-full items-center gap-2.5 rounded-md px-2 py-1.5 text-left hover:bg-white/[0.045]"><span className="grid size-7 place-items-center rounded-full bg-[#4d5249] text-[10px] font-semibold text-[#e4e6db]">SR</span><span className="min-w-0"><span className="block truncate text-[12px] font-medium text-[#dedfd9]">Sonia Rao</span><span className="block truncate text-[11px] text-[#7e807b]">Admin · Acme</span></span><Icon name="more" className="ml-auto size-4 text-[#858781]" /></button></div>
    </aside>
    <main className="min-w-0 flex-1"><header className="flex h-[68px] items-center justify-between border-b border-white/[0.08] px-5 sm:px-8"><a href="#overview" className="flex items-center gap-2 text-[15px] font-semibold tracking-[-0.03em] lg:hidden"><Mark /> Northstar</a><div className="hidden w-full max-w-[408px] items-center gap-2 rounded-md border border-white/[0.09] bg-white/[0.025] px-3 py-2 sm:flex"><Icon name="search" className="size-4 text-[#858781]" /><span className="flex-1 text-[12px] text-[#777974]">Search documents</span><kbd className="rounded border border-white/[0.1] px-1.5 py-0.5 font-mono text-[10px] text-[#858781]">⌘ K</kbd></div><div className="flex items-center gap-3"><div className="hidden items-center gap-2 text-[12px] text-[#969892] sm:flex"><span className="size-1.5 rounded-full bg-[#a9d65e] shadow-[0_0_8px_rgba(169,214,94,0.6)]" />All systems operational</div><button className="grid size-8 place-items-center rounded-md border border-white/[0.09] text-[#a4a69f] sm:hidden"><Icon name="search" className="size-4" /></button></div></header>
      <div className="mx-auto max-w-[1260px] px-5 py-8 sm:px-8 sm:py-11"><div className="flex flex-col justify-between gap-5 md:flex-row md:items-end"><div><p className="text-[11px] font-medium uppercase tracking-[0.16em] text-[#858781]">Acme Corporation · Knowledge workspace</p><h1 className="mt-2 text-[28px] font-medium tracking-[-0.045em] text-[#f1f1ec] sm:text-[34px]">Good morning, Sonia.</h1><p className="mt-2 text-[13px] text-[#92948e]">Your knowledge base is ready to be searched and shared.</p></div><button className="inline-flex h-9 items-center justify-center gap-2 rounded-md bg-[#d7ee66] px-3.5 text-[12px] font-semibold text-[#20220f] transition-colors hover:bg-[#e2f57b]"><Icon name="plus" className="size-4" />Add documents</button></div>
        <section className="mt-9 grid gap-px overflow-hidden rounded-lg border border-white/[0.09] bg-white/[0.09] sm:grid-cols-3" aria-label="Workspace metrics">{[["Indexed documents", "248", "+12 this week"], ["Searches this month", "1,842", "18% ↑"], ["Answer confidence", "94.6%", "Healthy"]].map(([label, value, detail]) => <div key={label} className="bg-[#121314] px-5 py-5"><p className="text-[11px] font-medium uppercase tracking-[0.12em] text-[#7d7f79]">{label}</p><div className="mt-3 flex items-end justify-between"><p className="text-[30px] font-medium tracking-[-0.05em] text-[#f0f0eb]">{value}</p><p className="text-[11px] text-[#a9d65e]">{detail}</p></div></div>)}</section>
        <section className="mt-7 grid gap-7 xl:grid-cols-[minmax(0,1fr)_320px]"><div id="documents" className="rounded-lg border border-white/[0.09] bg-[#121314]"><div className="flex items-center justify-between border-b border-white/[0.08] px-5 py-4"><div><h2 className="text-[14px] font-medium text-[#e8e9e3]">Recent documents</h2><p className="mt-0.5 text-[11px] text-[#7e807a]">Updated across this tenant</p></div><a href="#documents" className="flex items-center gap-1 text-[12px] font-medium text-[#cbdc73] hover:text-[#dced8a]">View all <Icon name="arrow" className="size-3.5" /></a></div><div className="overflow-x-auto"><table className="w-full min-w-[570px] text-left"><thead className="border-b border-white/[0.06] text-[10px] font-medium uppercase tracking-[0.12em] text-[#6f716c]"><tr><th className="px-5 py-3">Document</th><th className="px-4 py-3">Type</th><th className="px-4 py-3">Size</th><th className="px-5 py-3">Status</th></tr></thead><tbody className="divide-y divide-white/[0.06]">{documents.map((doc) => <tr key={doc.name} className="transition-colors hover:bg-white/[0.025]"><td className="px-5 py-3.5"><div className="flex items-center gap-3"><span className="grid size-7 place-items-center rounded border border-white/[0.08] bg-white/[0.035] text-[#9ea098]"><Icon name="file" className="size-3.5" /></span><span className="text-[12px] font-medium text-[#ddded8]">{doc.name}</span></div></td><td className="px-4 py-3.5 text-[11px] text-[#858780]">{doc.type}</td><td className="px-4 py-3.5 text-[11px] text-[#858780]">{doc.size}</td><td className="px-5 py-3.5"><span className={`inline-flex items-center gap-1.5 text-[11px] ${doc.tone === "ready" ? "text-[#a9d65e]" : "text-[#e5c86c]"}`}><span className={`size-1.5 rounded-full ${doc.tone === "ready" ? "bg-[#a9d65e]" : "bg-[#e5c86c]"}`} />{doc.state}</span></td></tr>)}</tbody></table></div></div>
          <div className="space-y-5"><section className="rounded-lg border border-white/[0.09] bg-[#121314] p-5"><div className="flex items-center justify-between"><h2 className="text-[14px] font-medium text-[#e8e9e3]">Ingestion activity</h2><Icon name="clock" className="size-4 text-[#7e807a]" /></div><div className="mt-5 border-l border-white/[0.1] pl-4"><div className="relative pb-5"><span className="absolute -left-[19px] top-1 size-2 rounded-full border-2 border-[#121314] bg-[#a9d65e]" /><p className="text-[12px] text-[#d9dad4]">Q3 operating plan</p><p className="mt-1 text-[11px] text-[#7d7f79]">Embedding chunks · 3 minutes ago</p></div><div className="relative"><span className="absolute -left-[19px] top-1 size-2 rounded-full border-2 border-[#121314] bg-[#5e625c]" /><p className="text-[12px] text-[#d9dad4]">Employee handbook</p><p className="mt-1 text-[11px] text-[#7d7f79]">Index completed · 28 minutes ago</p></div></div></section><section id="people-and-access" className="rounded-lg border border-[#c4dd55]/25 bg-[#161b10] p-5"><div className="flex items-start gap-3"><span className="mt-0.5 grid size-7 shrink-0 place-items-center rounded-md bg-[#cfe95f] text-[#1d210d]"><Icon name="check" className="size-4" /></span><div><h2 className="text-[13px] font-medium text-[#e9edcf]">Access is protected</h2><p className="mt-1.5 text-[11px] leading-5 text-[#abb08c]">Queries are filtered by tenant, department, role, and document permission.</p><a href="#people-and-access" className="mt-3 inline-flex items-center gap-1 text-[11px] font-medium text-[#d7eb71] hover:text-[#e6f798]">Review access rules <Icon name="arrow" className="size-3.5" /></a></div></div></section></div>
        </section></div>
    </main>
  </div></div>;
}

export default function Home() {
  const [authenticated, setAuthenticated] = useState(false);
  return authenticated ? <Dashboard onSignOut={() => setAuthenticated(false)} /> : <SignIn onEnter={() => setAuthenticated(true)} />;
}
