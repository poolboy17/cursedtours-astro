import { readFileSync, readdirSync } from 'fs';
import { join } from 'path';

export interface Article {
  title: string;
  slug: string;
  content: string;
  excerpt: string;
  date: string;
  modified?: string;
  featuredImage: {
    sourceUrl: string;
    altText: string;
    width?: number;
    height?: number;
  };
  categories: {
    id: number;
    slug: string;
    name: string;
    description?: string;
  }[];
  wordCount?: number;
  readingTime?: number;
  articleType?: 'pillar' | 'cluster';
  keywords?: string[];
}

export interface CategoryInfo {
  slug: string;
  name: string;
  description: string;
  hubPage?: string;
  count?: number;
}

export const CATEGORIES: Record<string, CategoryInfo> = {
  'salem-witch-trials': {
    slug: 'salem-witch-trials',
    name: 'Salem Witch Trials',
    description: 'In-depth articles exploring the history, causes, trials, and lasting legacy of the Salem Witch Trials of 1692.',
    hubPage: '/salem-ghost-tours/',
  },
  'new-orleans-voodoo-haunted-history': {
    slug: 'new-orleans-voodoo-haunted-history',
    name: 'New Orleans Voodoo & Haunted History',
    description: 'The true stories behind New Orleans\' haunted reputation—from Voodoo queens and pirate ghosts to the city\'s most documented hauntings.',
    hubPage: '/new-orleans-ghost-tours/',
  },
  'dracula-gothic-literature': {
    slug: 'dracula-gothic-literature',
    name: 'Dracula & Gothic Literature',
    description: 'Exploring the real history behind Bram Stoker\'s Dracula, Vlad the Impaler, vampire mythology, and the gothic literary tradition.',
    hubPage: '/destinations/draculas-castle/',
  },
  'chicago-haunted-history': {
    slug: 'chicago-haunted-history',
    name: 'Chicago Haunted History',
    description: 'The dark history behind Chicago\'s hauntings—from the Great Fire and H.H. Holmes to Resurrection Mary, gangster ghosts, and the city\'s most haunted landmarks.',
    hubPage: '/chicago-ghost-tours/',
  },
  'tour-planning': {
    slug: 'tour-planning',
    name: 'Tour Planning',
    description: 'Practical guides to help you choose, prepare for, and get the most out of ghost tours anywhere in the world.',
  },
};

let _cache: Article[] | null = null;

export function getAllArticles(): Article[] {
  if (_cache) return _cache;

  const dir = join(process.cwd(), 'src/data/articles');
  const files = readdirSync(dir).filter(f => f.endsWith('.json'));

  _cache = files.map(file => {
    const raw = readFileSync(join(dir, file), 'utf-8');
    const data = JSON.parse(raw);
    const img = typeof data.featuredImage === 'string'
      ? { sourceUrl: data.featuredImage, altText: data.title }
      : data.featuredImage;
    return {
      title: data.title,
      slug: data.slug,
      content: data.content,
      excerpt: data.excerpt || '',
      date: data.date,
      modified: data.modified,
      featuredImage: img,
      categories: data.categories.map((c: any) =>
        typeof c === 'string' ? { id: 0, slug: c, name: c } : c
      ),
      wordCount: data.wordCount,
      readingTime: data.readingTime,
      articleType: data.articleType,
      keywords: data.keywords,
    };
  }).sort((a, b) => new Date(b.date).getTime() - new Date(a.date).getTime());

  return _cache;
}

export function getArticlesByCategory(categorySlug: string): Article[] {
  return getAllArticles().filter(a =>
    a.categories.some(c => c.slug === categorySlug)
  );
}

export function getArticle(slug: string): Article | undefined {
  return getAllArticles().find(a => a.slug === slug);
}

export function getRelatedArticles(article: Article, limit = 4): Article[] {
  const catSlugs = article.categories.map(c => c.slug);
  return getAllArticles()
    .filter(a => a.slug !== article.slug && a.categories.some(c => catSlugs.includes(c.slug)))
    .slice(0, limit);
}
