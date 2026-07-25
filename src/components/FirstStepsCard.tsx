'use client';

import React from 'react';
import { useStore } from '@/store/useStore';
import { Briefcase, User, Play } from 'lucide-react';

interface FirstStepsCardProps {
  citizenSteps: string[];
  lawyerSteps: string[];
}

export const FirstStepsCard: React.FC<FirstStepsCardProps> = ({ citizenSteps, lawyerSteps }) => {
  const { isLawyerMode, setIsLawyerMode } = useStore();

  const activeSteps = isLawyerMode ? lawyerSteps : citizenSteps;

  return (
    <div className="bg-[#0b0b0b] border border-wine/20 rounded p-6 flex flex-col space-y-5 hover:border-wine/40 transition duration-300">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 border-b border-wine/10 pb-4">
        <div className="flex items-center space-x-2.5">
          {isLawyerMode ? (
            <Briefcase className="w-5 h-5 text-wine" />
          ) : (
            <User className="w-5 h-5 text-wine" />
          )}
          <h3 className="font-serif text-xl font-bold tracking-wide text-white">
            {isLawyerMode ? 'Tactical Legal Strategy' : 'Immediate Citizen Actions'}
          </h3>
        </div>

        <button
          onClick={() => setIsLawyerMode(!isLawyerMode)}
          className="flex items-center space-x-1 border border-wine/30 px-3 py-1.5 rounded text-xs uppercase font-mono tracking-wider font-semibold text-wine hover:text-white hover:border-wine hover:bg-wine/10 transition duration-200 self-start sm:self-auto"
        >
          <span>{isLawyerMode ? 'Citizen Mode' : 'Lawyer Mode?'}</span>
        </button>
      </div>

      <div className="space-y-3">
        {activeSteps && activeSteps.length > 0 ? (
          activeSteps.map((step, idx) => (
            <div key={idx} className="flex items-start space-x-3 text-sm text-gray-300 leading-relaxed">
              <span className="mt-1 flex items-center justify-center bg-wine/10 text-wine rounded-full p-1 border border-wine/20">
                <Play className="w-2.5 h-2.5 fill-wine" />
              </span>
              <span>{step}</span>
            </div>
          ))
        ) : (
          <p className="text-gray-500 text-xs italic">No steps recommended for this case.</p>
        )}
      </div>
    </div>
  );
};
