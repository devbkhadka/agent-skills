import React, { useState } from 'react';

export interface AccordionItemData {
  id: string;
  title: React.ReactNode;
  content: React.ReactNode;
  disabled?: boolean;
}

export interface AccordionProps {
  items: AccordionItemData[];
  defaultExpandedIds?: string[];
  allowMultiple?: boolean;
  className?: string;
}

export const Accordion: React.FC<AccordionProps> = ({
  items,
  defaultExpandedIds = [],
  allowMultiple = false,
  className = '',
}) => {
  const [expandedIds, setExpandedIds] = useState<string[]>(defaultExpandedIds);

  const toggleItem = (id: string) => {
    setExpandedIds((prev) => {
      if (prev.includes(id)) {
        return prev.filter((item) => item !== id);
      } else {
        return allowMultiple ? [...prev, id] : [id];
      }
    });
  };

  return (
    <div className={`vfd-accordion ${className}`.trim()}>
      {items.map((item) => {
        const isExpanded = expandedIds.includes(item.id);

        return (
          <div key={item.id} className="vfd-accordion-item">
            <button
              type="button"
              className="vfd-accordion-header"
              onClick={() => !item.disabled && toggleItem(item.id)}
              aria-expanded={isExpanded}
              disabled={item.disabled}
            >
              <span>{item.title}</span>
              <span className={`vfd-accordion-chevron ${isExpanded ? 'is-open' : ''}`}>▼</span>
            </button>
            {isExpanded && <div className="vfd-accordion-body">{item.content}</div>}
          </div>
        );
      })}
    </div>
  );
};
