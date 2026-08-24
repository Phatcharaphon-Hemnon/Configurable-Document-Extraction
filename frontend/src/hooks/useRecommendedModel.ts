import { useEffect, useState } from 'react';
import { fetchApiRoot } from '../api/client';

export function useRecommendedModel(fallback = 'Model'): string {
  const [modelName, setModelName] = useState(fallback);

  useEffect(() => {
    let cancelled = false;
    fetchApiRoot().then((root) => {
      const name = root?.recommended_extraction_model?.display_name;
      if (!cancelled && name) setModelName(name);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  return modelName;
}
