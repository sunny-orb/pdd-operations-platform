import type { ReactNode } from "react";
export function SectionHeading({
  eyebrow,
  title,
  extra,
  description,
}: {
  eyebrow?: string;
  title: string;
  extra?: ReactNode;
  description?: string;
}) {
  return (
    <div className="product-section-heading">
      <div>
        {eyebrow && <span className="product-eyebrow">{eyebrow}</span>}
        <h2>{title}</h2>
        {description && <p className="muted">{description}</p>}
      </div>
      {extra && <div className="section-heading-extra">{extra}</div>}
    </div>
  );
}
