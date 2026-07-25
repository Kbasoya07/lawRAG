import React from 'react';

interface ConfidenceBadgeProps {
  score: number; // 0.0 to 1.0 or percentage 0 to 100
}

export const ConfidenceBadge: React.FC<ConfidenceBadgeProps> = ({ score }) => {
  // Normalize score to percentage and handle undefined/NaN gracefully
  const validScore = typeof score === 'number' && !isNaN(score) ? score : 0.70;
  const percentage = validScore <= 1.0 ? Math.round(validScore * 100) : Math.round(validScore);

  let badgeColor = '';
  if (percentage >= 85) {
    badgeColor = 'bg-emerald-950/40 text-emerald-400 border border-emerald-500/30';
  } else if (percentage >= 70) {
    badgeColor = 'bg-amber-950/40 text-amber-400 border border-amber-500/30';
  } else {
    badgeColor = 'bg-rose-950/40 text-rose-400 border border-rose-500/30';
  }

  return (
    <span className={`px-2.5 py-1 rounded text-xs font-mono font-semibold tracking-wider uppercase ${badgeColor}`}>
      {percentage}% Confidence
    </span>
  );
};
