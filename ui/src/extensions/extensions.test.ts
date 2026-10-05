import { describe, expect, it } from 'vitest';

import { communityExtensionRegistry } from './community';
import {
  composeExtensionRegistries,
  validateExtensionRegistry,
  type TorqueExtensionRegistry,
} from './types';

describe('extension registry', () => {
  it('ships an empty community registry', () => {
    expect(validateExtensionRegistry(communityExtensionRegistry).panels).toEqual([]);
  });

  it('fails closed for invalid or duplicate panel ids', () => {
    const Component = () => null;
    const registry: TorqueExtensionRegistry = {
      version: 1,
      panels: [
        {
          id: 'remote',
          title: 'Remote',
          defaultPlacement: 'right',
          load: () => Promise.resolve({ default: Component }),
        },
        {
          id: 'remote',
          title: 'Duplicate',
          defaultPlacement: 'bottom',
          load: () => Promise.resolve({ default: Component }),
        },
      ],
    };
    expect(() => validateExtensionRegistry(registry)).toThrow('Duplicate extension panel id');
    expect(() => validateExtensionRegistry({
      version: 1,
      panels: [{
        id: '../bad',
        title: 'Bad',
        defaultPlacement: 'right',
        load: () => Promise.resolve({ default: Component }),
      }],
    })).toThrow('Invalid extension panel id');
    expect(() => validateExtensionRegistry({
      version: 1,
      panels: [{
        id: 'board',
        title: 'Core collision',
        defaultPlacement: 'right',
        load: () => Promise.resolve({ default: Component }),
      }],
    }, new Set(['board']))).toThrow('Duplicate extension panel id');
  });

  it('validates and composes an optional product registry without importing it in community builds', () => {
    const Component = () => null;
    const optionalRegistry: TorqueExtensionRegistry = {
      version: 1,
      panels: [{
        id: 'remote-control',
        title: 'Remote control',
        defaultPlacement: 'right',
        requiredRuntimeCapabilities: ['remote.read'],
        load: () => Promise.resolve({ default: Component }),
      }],
    };
    expect(composeExtensionRegistries([communityExtensionRegistry, optionalRegistry]).panels)
      .toHaveLength(1);
    expect(() => composeExtensionRegistries([optionalRegistry, optionalRegistry]))
      .toThrow('Duplicate extension panel id');
    expect(() => validateExtensionRegistry({
      version: 1,
      panels: [{
        id: 'unsafe-remote',
        title: 'Unsafe remote',
        defaultPlacement: 'right',
        requiredRuntimeCapabilities: ['../unsafe'],
        load: () => Promise.resolve({ default: Component }),
      }],
    })).toThrow('Invalid extension runtime capability');
  });
});
