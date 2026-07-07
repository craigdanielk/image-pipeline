import type { CSSProperties, ReactNode } from "react";

type Props = {
  children: ReactNode;
  active?: boolean;
  className?: string;
  as?: "div" | "section" | "article";
  style?: CSSProperties;
};

export default function GlassCard({
  children,
  active = false,
  className = "",
  as: Tag = "div",
  style,
}: Props) {
  return (
    <Tag
      className={`glass-card ${active ? "glass-card--active" : ""} ${className}`.trim()}
      style={style}
    >
      {children}
    </Tag>
  );
}
