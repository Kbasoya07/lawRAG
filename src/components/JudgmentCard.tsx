import React from 'react';
import { ExternalLink, Scale } from 'lucide-react';

interface JudgmentCardProps {
  caseTitle: string;
  court: string;
  hearingDate: string;
  verdict: string;
  similarityScore: number;
  observation: string;
  sourceUrl?: string;
}

export const JudgmentCard: React.FC<JudgmentCardProps> = ({
  caseTitle,
  court,
  hearingDate,
  verdict,
  similarityScore,
  observation,
  sourceUrl,
}) => {
  const percentage = Math.round(similarityScore * 100);

  return (
    <div className="bg-[#0b0b0b] border border-wine/20 rounded p-6 flex flex-col space-y-4 hover:border-wine/40 transition duration-300">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 border-b border-wine/10 pb-3">
        <div>
          <h4 className="font-serif text-lg font-bold text-white flex items-center space-x-2">
            <Scale className="w-5 h-5 text-wine" />
            <span>{caseTitle}</span>
          </h4>
          <p className="text-xs text-gray-500 mt-0.5">
            {court} | Decided: {hearingDate}
          </p>
        </div>
        <div className="flex items-center space-x-1.5 self-start">
          <span className="text-xs text-gray-400 font-mono">Similarity:</span>
          <span className="text-sm font-bold text-wine font-mono">{percentage}%</span>
        </div>
      </div>

      <div className="space-y-2">
        <div className="text-xs">
          <span className="font-semibold text-wine tracking-wider uppercase block mb-1">Key Verdict:</span>
          <span className="text-gray-200">{verdict}</span>
        </div>

        <div className="text-sm text-gray-400 leading-relaxed italic border-l border-wine/30 pl-3">
          &ldquo;{observation}&rdquo;
        </div>
      </div>

      {sourceUrl && (
        <a
          href={sourceUrl}
          target="_blank"
          rel="noopener noreferrer"
          className="flex items-center space-x-1 text-xs text-wine hover:text-white uppercase font-mono tracking-wider font-semibold self-start transition duration-200"
        >
          <span>View Source Kanoon</span>
          <ExternalLink className="w-3.5 h-3.5" />
        </a>
      )}
    </div>
  );
};
