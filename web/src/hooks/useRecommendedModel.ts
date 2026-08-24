import { useEffect, useState } from 'react';
import { fetchApiRoot } from '../api/client';

export function useRecommendedModel(fallback = 'Nemotron VL'): string {
  const [modelName, setModelName] = useState(fallback);

  useEffect(() => {
    let cancelled = false;
    fetchApiRoot().then((root) => {
      const name = root?.extraction_model;
      if (!cancelled && name) setModelName(name);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  return modelName;
}
