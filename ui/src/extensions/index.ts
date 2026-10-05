import { communityExtensionRegistry } from './community';
import { composeExtensionRegistries } from './types';

export const extensionRegistry = composeExtensionRegistries([communityExtensionRegistry]);

export * from './types';
