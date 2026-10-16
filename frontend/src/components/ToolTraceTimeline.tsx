import { useState, useMemo } from "react";
import type { ToolTrace } from "../entities/Message";
import {
  Collapsible,
  CollapsibleTrigger,
  CollapsibleContent,
} from "./ui/collapsible";
import { Badge } from "./ui/badge";
import {
  Wrench,
  CheckCircle2,
  AlertCircle,
  Loader2,
  ChevronDown,
  GitBranch,
  Network,
  Search,
  Code2,
} from "lucide-react";

interface ToolTraceTimelineProps {
  tools?: ToolTrace[];
  isStreaming?: boolean;
}

const getToolCategory = (toolName: string) => {
  const name = toolName.toLowerCase();
  if (
    name.includes("risk") ||
    name.includes("call_graph") ||
    name.includes("execution") ||
    name.includes("architecture") ||
    name.includes("algo")
  ) {
    return {
      label: "FalkorDB Algo",
      badgeClass:
        "text-purple-400 border-purple-500/30 bg-purple-500/10 dark:text-purple-300",
      icon: Network,
    };
  }
  if (
    name.includes("issue") ||
    name.includes("pull_request") ||
    name.includes("branch") ||
    name.includes("push") ||
    name.includes("mcp")
  ) {
    return {
      label: "MCP Action",
      badgeClass:
        "text-emerald-500 border-emerald-500/30 bg-emerald-500/10 dark:text-emerald-400",
      icon: GitBranch,
    };
  }
  if (name.includes("search")) {
    return {
      label: "Search",
      badgeClass:
        "text-amber-500 border-amber-500/30 bg-amber-500/10 dark:text-amber-400",
      icon: Search,
    };
  }
  return {
    label: "Code Graph",
    badgeClass:
      "text-blue-500 border-blue-500/30 bg-blue-500/10 dark:text-blue-400",
    icon: Code2,
  };
};

export const ToolTraceTimeline = ({
  tools,
  isStreaming,
}: ToolTraceTimelineProps) => {
  const [isOpen, setIsOpen] = useState(false);

  const hasRunning = useMemo(
    () => tools?.some((t) => t.status === "running"),
    [tools],
  );

  const runningTool = useMemo(
    () => tools?.find((t) => t.status === "running"),
    [tools],
  );

  if (!tools || tools.length === 0) return null;

  return (
    <Collapsible
      open={isOpen}
      onOpenChange={setIsOpen}
      className="my-2.5 w-full max-w-2xl"
    >
      <CollapsibleTrigger className="flex items-center gap-2 text-xs font-medium px-3 py-1.5 rounded-lg bg-muted/60 hover:bg-muted border border-border/50 text-foreground transition-all duration-150 select-none group w-full text-left">
        {hasRunning ? (
          <Loader2 className="size-3.5 animate-spin text-primary shrink-0" />
        ) : (
          <Wrench className="size-3.5 text-primary shrink-0" />
        )}

        <span className="truncate">
          {hasRunning ? (
            <span className="text-primary font-medium">
              Executing <code className="font-mono">{runningTool?.name}</code>
              ...
            </span>
          ) : (
            <span>
              {tools.length} tool{tools.length > 1 ? "s" : ""} executed
            </span>
          )}
        </span>

        <div className="flex items-center gap-1.5 ml-auto shrink-0">
          {!hasRunning && (
            <div className="hidden sm:flex items-center gap-1">
              {tools.slice(0, 2).map((t) => (
                <span
                  key={t.id}
                  className="font-mono text-[10px] text-muted-foreground bg-background/80 px-1.5 py-0.5 rounded border border-border/40"
                >
                  {t.name}
                </span>
              ))}
              {tools.length > 2 && (
                <span className="text-[10px] text-muted-foreground">
                  +{tools.length - 2}
                </span>
              )}
            </div>
          )}

          <ChevronDown
            className={`size-3.5 text-muted-foreground transition-transform duration-200 ${
              isOpen ? "rotate-180" : ""
            }`}
          />
        </div>
      </CollapsibleTrigger>

      <CollapsibleContent className="mt-2.5 pl-3 border-l-2 border-primary/30 space-y-3 py-1">
        {tools.map((step, idx) => {
          const category = getToolCategory(step.name);
          const CategoryIcon = category.icon;

          return (
            <div key={step.id || idx} className="text-xs space-y-1">
              <div className="flex items-center gap-2 flex-wrap">
                {step.status === "running" ? (
                  <Loader2 className="size-3 animate-spin text-primary shrink-0" />
                ) : step.status === "error" ? (
                  <AlertCircle className="size-3 text-rose-500 shrink-0" />
                ) : (
                  <CheckCircle2 className="size-3 text-emerald-500 shrink-0" />
                )}

                <span className="font-mono font-medium text-foreground">
                  {step.name}
                </span>

                <Badge
                  variant="outline"
                  className={`text-[10px] px-1.5 py-0 h-4 font-normal gap-1 ${category.badgeClass}`}
                >
                  <CategoryIcon className="size-2.5" />
                  {category.label}
                </Badge>
              </div>

              {step.summary && (
                <p className="text-[11px] text-muted-foreground font-mono pl-5 leading-relaxed">
                  {step.summary}
                </p>
              )}

              {step.args && Object.keys(step.args).length > 0 && (
                <div className="pl-5 text-[10px] text-muted-foreground/70 font-mono overflow-x-auto">
                  <span className="text-muted-foreground/50">args: </span>
                  {JSON.stringify(step.args)}
                </div>
              )}
            </div>
          );
        })}
      </CollapsibleContent>
    </Collapsible>
  );
};

export default ToolTraceTimeline;
