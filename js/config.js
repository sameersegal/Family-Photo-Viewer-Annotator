/**
 * Family Photo Album - Configuration
 *
 * Update these settings for your deployment:
 * - Set imageSource to 'r2' and fill in R2 details for production
 * - Fill in Firebase config for shared annotations
 * - Leave Firebase apiKey empty to use localStorage (single device only)
 */
export const CONFIG = {
  // Image source: 'local' (./images/) or 'r2' (Cloudflare R2)
  imageSource: 'local',

  // Cloudflare R2 settings
  r2: {
    publicUrl: '',       // e.g. 'https://photos.your-domain.com'
    fullPath: '/full',
    thumbPath: '/thumbs',
  },

  // Local image settings
  localImagePath: './images/',
  localThumbPath: './images/',

  // Firebase configuration (leave apiKey empty to use localStorage)
  firebase: {
    apiKey: '',
    authDomain: '',
    projectId: '',
    storageBucket: '',
    messagingSenderId: '',
    appId: '',
  },

  // Slideshow settings
  slideshow: {
    autoAdvanceMs: 6000,
    fadeMs: 1500,
    preloadCount: 3,
    showAnnotations: true,
    annotationDisplayMs: 4000,
  },

  // App settings
  app: {
    title: 'Our Family Album',
    subtitle: 'Photos & Memories',
    frameImage: 'assets/frame.png',
  },
};
