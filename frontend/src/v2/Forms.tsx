import { useEffect, useState, type FormEvent } from "react";
import { Plus, FlaskConical, RotateCcw } from "lucide-react";
import { createCommand, isUncertain } from "./api";
import { useCommand } from "./queries";
import { planExperiment, useExperiment, type PairResult } from "./experiment";
import type { Snapshot } from "./types";
import { randomUUID } from "./uuid";

export function ErrorNotice({ error, retry }: { error: Error | null; retry?: () => void }) {
  if (!error) return null;
  return (
    <div className="lab-error" role="alert">
      <strong>操作未完成</strong>
      <p>{error.message}</p>
      {retry && isUncertain(error) && (
        <p>暂时没收到结果，操作可能已经完成。重试会取回这次操作，不会重复推进。</p>
      )}
      {retry && isUncertain(error) && (
        <button type="button" onClick={retry}>
          取回这次操作
        </button>
      )}
    </div>
  );
}
export function CreateWorldForm({
  onCreated,
  blocked = false,
  onBusy,
}: {
  onCreated: (snapshot: Snapshot) => void;
  blocked?: boolean;
  onBusy?: (busy: boolean) => void;
}) {
  const mutation = useCommand();
  const creating = mutation.isPending || isUncertain(mutation.error);
  useEffect(() => {
    onBusy?.(creating);
    return () => onBusy?.(false);
  }, [creating, onBusy]);
  const [id, setId] = useState(() => `world-${randomUUID().slice(0, 8)}`);
  const [timeline, setTimeline] = useState("main");
  const [seed, setSeed] = useState(() => String(crypto.getRandomValues(new Uint32Array(1))[0]));
  const [size, setSize] = useState("16x8");
  const [validation, setValidation] = useState<Error | null>(null);
  const submit = (event: FormEvent) => {
    event.preventDefault();
    setValidation(null);
    const parsed = Number(seed);
    if (!/^\d+$/.test(seed) || !Number.isSafeInteger(parsed)) {
      setValidation(new Error("世界种子请填写 0 到 9007199254740991 之间的整数。"));
      return;
    }
    const [width, height] = size.split("x").map(Number);
    mutation.mutate(
      createCommand({
        world_id: id,
        timeline_id: timeline,
        seed: parsed,
        width,
        height,
        max_species: 32,
      }),
      { onSuccess: (created) => {
          onCreated(created);
          setId(`world-${randomUUID().slice(0, 8)}`);
          setSeed(String(crypto.getRandomValues(new Uint32Array(1))[0]));
        } }
    );
  };
  return (
    <details className="lab-card lab-create" open>
      <summary>
        <Plus size={16} aria-hidden="true" /> 诞生一个世界
      </summary>
      <form onSubmit={submit}>
        <fieldset disabled={blocked || mutation.isPending || isUncertain(mutation.error)}>
          <label>
            世界大小
            <select value={size} onChange={(e) => setSize(e.target.value)}>
              <option value="4x2">微型 · 快速看看</option>
              <option value="8x4">小型 · 轻松游玩</option>
              <option value="16x8">标准 · 更多栖息地</option>
            </select>
          </label>
          <details className="play-create-advanced">
            <summary>自定义世界</summary>
            <label>
              世界代号
              <input
                required
                pattern="[A-Za-z0-9][A-Za-z0-9_.-]{0,63}"
                maxLength={64}
                value={id}
                onChange={(e) => setId(e.target.value)}
              />
            </label>
            <label>
              初始分支代号
              <input required pattern="[A-Za-z0-9][A-Za-z0-9_.-]{0,63}" maxLength={64}
                value={timeline} onChange={(e) => setTimeline(e.target.value)} />
            </label>
            <label>
              世界种子
              <input required inputMode="numeric" value={seed} onChange={(e) => setSeed(e.target.value)} />
            </label>
            <p className="lab-note">相同种子和大小，会带来相同的初始世界。</p>
          </details>
          <button className="lab-primary" type="submit">
            {mutation.isPending ? "生命正在萌芽…" : "开始新世界"}
          </button>
        </fieldset>
      </form>
      <ErrorNotice
        error={validation ?? mutation.error}
        retry={
          !validation && mutation.variables
            ? () => mutation.mutate(mutation.variables!, { onSuccess: onCreated })
            : undefined
        }
      />
      <p className="lab-note">世界会自动保存，下次可以接着玩。</p>
    </details>
  );
}
export function ExperimentForm({
  snapshot,
  busy,
  onComplete,
  onBlockingChange,
}: {
  snapshot: Snapshot;
  busy: boolean;
  onComplete: (pair: PairResult) => void;
  onBlockingChange: (value: boolean) => void;
}) {
  const [prefix, setPrefix] = useState("trial-1");
  const [error, setError] = useState<Error | null>(null);
  const mutation = useExperiment();
  const blocked = mutation.isPending || isUncertain(mutation.error);
  useEffect(() => {
    onBlockingChange(blocked);
    return () => onBlockingChange(false);
  }, [blocked, onBlockingChange]);
  const submit = (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    try {
      mutation.mutate(planExperiment(snapshot, prefix), { onSuccess: onComplete });
    } catch (caught) {
      setError(caught as Error);
    }
  };
  return (
    <section className="lab-card">
      <h3>
        <FlaskConical size={17} aria-hidden="true" /> +4°C 配对实验
      </h3>
      <p>
        从当前查看的回合 <strong>{snapshot.turn}</strong> 复制两个分支，各推进一回合。共享 control
        分支的 RNG 命名空间。
      </p>
      <p className="lab-note">
        实验分支的升温偏移在基线上增加4°C；实际全球温度按参考模型逐步响应。
      </p>
      <form onSubmit={submit}>
        <fieldset disabled={busy || blocked}>
          <label>
            实验标识
            <input
              required
              pattern="[A-Za-z0-9][A-Za-z0-9_.-]{0,51}"
              maxLength={52}
              value={prefix}
              onChange={(e) => setPrefix(e.target.value)}
            />
          </label>
          <p className="lab-note">
            {prefix}-control / {prefix}-warm
          </p>
          <button type="submit">
            {mutation.isPending ? "正在创建并推进分支…" : "建立配对实验"}
          </button>
        </fieldset>
      </form>
      <ErrorNotice
        error={error ?? mutation.error}
        retry={
          !error && mutation.variables
            ? () => mutation.mutate(mutation.variables!, { onSuccess: onComplete })
            : undefined
        }
      />
      <p className="lab-note">分支分步提交。失败时已创建的分支仍保留；可从时间线列表检查。</p>
    </section>
  );
}
export function RewindConfirm({
  source,
  pending,
  onConfirm,
}: {
  source: Snapshot;
  pending: boolean;
  onConfirm: () => void;
}) {
  const [confirmed, setConfirmed] = useState(false);
  return (
    <div className="lab-rewind">
      <label className="lab-check">
        <input
          type="checkbox"
          checked={confirmed}
          onChange={(e) => setConfirmed(e.target.checked)}
        />
        回到回合 {source.turn}，让当前分支从这里重新演化。原来的历史仍会保留。
      </label>
      <button
        type="button"
        disabled={!confirmed || pending}
        onClick={() => {
          setConfirmed(false);
          onConfirm();
        }}
      >
        <RotateCcw size={14} aria-hidden="true" /> 从这一回合重来
      </button>
    </div>
  );
}
