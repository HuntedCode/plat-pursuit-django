"""Static-files storage (SEO closing audit).

The manifest variant is what makes WhiteNoise emit far-future immutable Cache-Control:
content-hashed names are safe to cache forever, and without them EVERY asset (output.css,
the self-hosted fonts) revalidates at max-age=60 on the critical path.

The subclass exists because Django's post-processor rewrites url()/@import/sourceMappingURL
references with a regex, not a parser, and raises MissingFileError for anything it cannot
resolve -- including vendored minified JS pointing at sourcemaps we never ship. Those are
not deploy-blockers: leave the reference as written and move on. References that MUST
resolve (the fonts in output.css) are pinned by tests/engine/test_seo_lane3.py instead.
"""
from whitenoise.storage import CompressedManifestStaticFilesStorage


class ForgivingManifestStaticFilesStorage(CompressedManifestStaticFilesStorage):
    def hashed_name(self, name, content=None, filename=None):
        try:
            return super().hashed_name(name, content, filename)
        except ValueError:
            # MissingFileError subclasses ValueError: the referenced file is not in the
            # collected set (vendor sourcemaps, mis-parsed comment paths). Serve the
            # reference verbatim rather than failing collectstatic.
            return name
