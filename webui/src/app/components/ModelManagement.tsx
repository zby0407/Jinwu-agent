"use client";

import { useState, type ComponentType } from "react";
import { KeyRound, Orbit, SlidersHorizontal } from "lucide-react";
import { ModelSettings } from "@/app/components/ModelSettings";
import { ScientificDataInterfaces } from "@/app/components/ScientificDataInterfaces";
import { cn } from "@/lib/utils";
import { useCollapseAgentActions } from "@/lib/uiSettings";

type SettingsSection = "models" | "interfaces" | "preferences";

const sections: Array<{
  id: SettingsSection;
  label: string;
  description: string;
  icon: ComponentType<{ className?: string }>;
}> = [
  { id: "models", label: "模型与 API", description: "云端、兼容接口与本地模型", icon: Orbit },
  { id: "interfaces", label: "识别 API", description: "PDF、图表识别与数据源发现服务", icon: KeyRound },
  { id: "preferences", label: "界面偏好", description: "会话界面显示偏好", icon: SlidersHorizontal },
];


function InterfacePreferences() {
  const { value, setValue } = useCollapseAgentActions();
  return (
    <div className="max-w-2xl rounded-xl border border-slate-200 bg-slate-50 p-4">
      <label className="flex items-start gap-3 text-sm leading-6 text-slate-700">
        <input
          type="checkbox"
          checked={value}
          onChange={(event) => setValue(event.target.checked)}
          aria-label="默认折叠 Agent 操作"
          className="mt-1.5 size-4 rounded border-slate-300 accent-sky-700"
        />
        <span>
          <span className="block font-medium text-slate-900">默认折叠 Agent 操作</span>
          <span className="mt-1 block text-xs leading-5 text-slate-500">
            工具调用序列在运行期间和完成后保持折叠，审批控件仍会显示。
          </span>
        </span>
      </label>
    </div>
  );
}

export function ModelManagement() {
  const [section, setSection] = useState<SettingsSection>("models");
  const active = sections.find((item) => item.id === section) ?? sections[0];

  return (
    <div className="min-h-full bg-slate-50 text-slate-900">
      <div className="mx-auto max-w-7xl px-4 py-5 sm:px-7 sm:py-7">
        <div role="tablist" aria-label="模型管理分类" className="mb-5 flex flex-wrap gap-2">
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

        {section === "models" ? (
          <ModelSettings embedded />
        ) : (
          <section aria-label={active.label} className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm sm:p-5">
            <div className="mb-4 border-b border-slate-200 pb-3">
              <h3 className="text-lg font-semibold tracking-tight">{active.label}</h3>
              <p className="mt-1 text-sm text-slate-600">{active.description}</p>
            </div>
            {section === "interfaces" && <ScientificDataInterfaces />}
            {section === "preferences" && <InterfacePreferences />}
          </section>
        )}
      </div>
    </div>
  );
}
