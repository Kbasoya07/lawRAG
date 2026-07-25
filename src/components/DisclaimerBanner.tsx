'use client';

import React, { useState } from 'react';
import { ShieldAlert, Check, X } from 'lucide-react';

export const DisclaimerBanner: React.FC = () => {
  const [selection, setSelection] = useState<'yes' | 'no' | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const handleSelection = async (value: 'yes' | 'no') => {
    setSelection(value);
    setIsSubmitting(true);
    
    // Simulate database storage call
    try {
      await new Promise(resolve => setTimeout(resolve, 800));
      console.log(`Stored advocate connection selection: ${value}`);
    } catch (e) {
      console.error(e);
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="fixed bottom-0 left-0 right-0 bg-[#060606] border-t border-wine/40 px-6 py-4 flex flex-col md:flex-row items-center justify-between gap-4 z-40 backdrop-blur-md">
      <div className="flex items-center space-x-2 text-xs md:text-sm text-gray-400">
        <ShieldAlert className="w-5 h-5 text-wine flex-shrink-0" />
        <p>
          <span className="font-semibold text-white">Disclaimer:</span> Educational research only. Consult a certified advocate.
        </p>
      </div>

      <div className="flex items-center space-x-4">
        <span className="text-xs md:text-sm text-gray-300 font-serif">
          Would you like if we could connect you with regional advocates? (In Future Updates)
        </span>

        {selection === null ? (
          <div className="flex items-center space-x-2">
            <button
              onClick={() => handleSelection('yes')}
              disabled={isSubmitting}
              className="px-3.5 py-1 bg-wine text-white text-xs rounded uppercase font-mono tracking-wider font-semibold hover:bg-wine-dark transition duration-200"
            >
              Yes
            </button>
            <button
              onClick={() => handleSelection('no')}
              disabled={isSubmitting}
              className="px-3.5 py-1 border border-gray-700 text-gray-400 text-xs rounded uppercase font-mono tracking-wider font-semibold hover:text-white hover:border-gray-500 transition duration-200"
            >
              No
            </button>
          </div>
        ) : (
          <div className="flex items-center space-x-1.5 text-xs font-mono font-semibold tracking-wider text-wine">
            {selection === 'yes' ? (
              <>
                <Check className="w-4 h-4 text-wine" />
                <span>Feedback Saved</span>
              </>
            ) : (
              <>
                <X className="w-4 h-4 text-gray-500" />
                <span>Declined</span>
              </>
            )}
          </div>
        )}
      </div>
    </div>
  );
};
