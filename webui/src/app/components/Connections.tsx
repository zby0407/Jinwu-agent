"use client";

import { useState, type ComponentType } from "react";
import { Database, FileText, Wrench } from "lucide-react";
import { DataSourceSettings } from "@/app/components/DataSourceSettings";
import { cn } from "@/lib/utils";

type ConnectionSection = "sources" | "literature" | "mcp";

const sections: Array<{
  id: ConnectionSection;
  label: string;
  description: string;
  icon: ComponentType<{ className?: string }>;
}> = [
  { id: "sources", label: "数据源", description: "观测数据源接入状态", icon: Database },
  { id: "literature", label: "文献源", description: "论文源订阅与文献 API 凭据", icon: FileText },
  { id: "mcp", label: "MCP 工具", description: "外部工具服务连接", icon: Wrench },
];

export function Connections() {
  const [section, setSection] = useState<ConnectionSection>("sources");
  const active = sections.find((item) => item.id === section) ?? sections[0];

  return (
    <div className="min-h-full bg-slate-50 text-slate-900">
      <div className="mx-auto max-w-7xl px-4 py-5 sm:px-7 sm:py-7">
        <div role="tablist" aria-label="连接分类" className="mb-5 flex flex-wrap gap-2">
          {sections.map((item) => (
            <button
              key={item.id}
              type="button"
              role="tab"
              aria-selected={section === item.id}
              onClick={() => setSection(item.id)}
              className={cn(
                "flex items-center gap-2 rounded-full border px-3.5 py-2 text-sm transition",
                section === item.id
                  ? "border-sky-300 bg-sky-50 font-medium text-sky-800 shadow-sm"
                  : "border-slate-200 bg-white text-slate-600 hover:border-slate-300 hover:text-slate-900"
              )}
            >
              <item.icon className="size-4" aria-hidden="true" />
              {item.label}
            </button>
          ))}
        </div>

        <section aria-label={active.label} className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm sm:p-5">
          <div className="mb-4 border-b border-slate-200 pb-3">
            <h3 className="text-lg font-semibold tracking-tight">{active.label}</h3>
            <p className="mt-1 text-sm text-slate-600">{active.description}</p>
          </div>
          {section === "sources" && (
            <div className="flex min-h-[620px] flex-col">
              <DataSourceSettings section="data" />
            </div>
          )}
          {section === "literature" && (
            <div className="flex min-h-[620px] flex-col">
              <DataSourceSettings section="literature" />
            </div>
          )}
          {section === "mcp" && (
            <p className="py-8 text-sm text-slate-500">
              独立 MCP 工具目录尚未接入。现有工具调用与审批可在科研助手的会话中查看。
            </p>
          )}
        </section>
      </div>
    </div>
  );
}
