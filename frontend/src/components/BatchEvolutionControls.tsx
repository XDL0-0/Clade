import { Pause } from "lucide-react";
import "./BatchEvolutionControls.css";

export function BatchEvolutionControls({ requested, onPause }: {
  requested: boolean;
  onPause: () => void;
}) {
  return (
    <div className="classic-batch-controls">
      <button type="button" disabled={requested} onClick={onPause}>
        <Pause size={16} aria-hidden="true" />
        {requested ? "将在本回合结束后暂停" : "本回合结束后暂停"}
      </button>
      <p role="status">
        {requested ? "等当前回合完成后，就能查看地图和演化结果。" : "每完成一回合，地图、物种和报告都会更新。"}
      </p>
    </div>
  );
}
