import { useState } from "react";
import { ErrorNotice } from "./Forms";

export function ScenarioJSON({
  disabled,
  onImport,
  onExport,
}: {
  disabled: boolean;
  onImport: (raw: string) => void;
  onExport: () => void;
}) {
  const [raw, setRaw] = useState("");
  const [error, setError] = useState<Error | null>(null);
  return (
    <details className="lab-scenario-json">
      <summary>JSON 导入 / 导出</summary>
      <label>
        打开 JSON 文件
        <input
          type="file"
          accept="application/json,.json"
          disabled={disabled}
          onChange={async (event) => {
            const file = event.target.files?.[0];
            if (!file) return;
            try {
              if (file.size > 4_000_000) throw new Error("计划超过 4 MB。");
              setRaw(await file.text());
              setError(null);
            } catch (caught) {
              setError(caught as Error);
            }
          }}
        />
      </label>
      <label>
        完整 ExperimentPlan JSON
        <textarea rows={10} value={raw} disabled={disabled} onChange={(e) => setRaw(e.target.value)} />
      </label>
      <div className="lab-scenario-actions">
        <button disabled={disabled} onClick={() => onImport(raw)}>导入草稿</button>
        <button onClick={onExport}>导出当前计划</button>
      </div>
      <ErrorNotice error={error} />
      <p className="lab-note">文件只填入草稿，不会自动开始。无法识别的字段和数值会保留并报错。</p>
    </details>
  );
}
