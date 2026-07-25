'use client';

import React, { useState } from 'react';
import { ConfidenceBadge } from './ConfidenceBadge';
import { ChevronDown, ChevronUp, Scroll } from 'lucide-react';

interface LawCardProps {
  sectionName: string; // e.g. "BNS 2023, Section 103"
  confidence: number;
  reasoning: string;
  verbatimCitation: string;
  evidenceNeeded: string[];
}

export const LawCard: React.FC<LawCardProps> = ({
  sectionName,
  confidence,
  reasoning,
  verbatimCitation,
  evidenceNeeded,
}) => {
  const [isExpanded, setIsExpanded] = useState(false);

  return (
    <div className="bg-[#0b0b0b] rounded border border-wine/20 p-6 flex flex-col space-y-4 transition duration-300 hover:border-wine/40">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-wine/10 pb-4">
        <h3 className="font-serif text-xl font-bold tracking-wide text-white flex items-center space-x-2">
          <Scroll className="w-5 h-5 text-wine" />
          <span>{sectionName}</span>
        </h3>
        <ConfidenceBadge score={confidence} />
      </div>

      <div className="text-gray-300 text-sm leading-relaxed">
        <p className="font-semibold text-gray-200 mb-1">Application Summary:</p>
        {reasoning}
      </div>

      {evidenceNeeded && evidenceNeeded.length > 0 && (
        <div className="bg-[#050505] p-4 rounded border border-gray-900 text-xs">
          <p className="font-semibold text-wine mb-2 uppercase tracking-wider">Required Evidence Checklist:</p>
          <ul className="list-disc pl-4 space-y-1 text-gray-400">
            {evidenceNeeded.map((item, idx) => (
              <li key={idx}>{item}</li>
            ))}
          </ul>
        </div>
      )}

      <div className="border-t border-wine/10 pt-4">
        <button
          onClick={() => setIsExpanded(!isExpanded)}
          className="flex items-center space-x-1.5 text-xs text-wine hover:text-white transition duration-200 uppercase font-mono tracking-wider font-semibold"
        >
          <span>{isExpanded ? 'Hide Citation' : 'Show Verbatim Citation'}</span>
          {isExpanded ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
        </button>

        {isExpanded && (
          <div className="mt-3 bg-[#030303] border border-wine/20 p-4 rounded text-xs text-gray-400 font-serif italic leading-relaxed border-l-4 border-l-wine">
            &ldquo;{verbatimCitation ? verbatimCitation.trim() : ''}&rdquo;
          </div>
        )}
      </div>
    </div>
  );
};
