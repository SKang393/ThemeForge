# noqa: SIZE_OK - shared constants table for lexical filters and transcript patterns.
from __future__ import annotations

import re

STOPWORDS = {
    "a",
    "about",
    "above",
    "after",
    "again",
    "against",
    "all",
    "already",
    "also",
    "am",
    "an",
    "and",
    "any",
    "are",
    "as",
    "at",
    "be",
    "because",
    "been",
    "before",
    "being",
    "below",
    "between",
    "both",
    "but",
    "by",
    "can",
    "can't",
    "cannot",
    "could",
    "definitely",
    "did",
    "do",
    "does",
    "doesn't",
    "doing",
    "down",
    "during",
    "each",
    "exactly",
    "few",
    "fine",
    "for",
    "from",
    "further",
    "had",
    "has",
    "have",
    "having",
    "he",
    "her",
    "here",
    "hers",
    "herself",
    "him",
    "himself",
    "his",
    "he's",
    "how",
    "i",
    "i'm",
    "i've",
    "if",
    "in",
    "into",
    "is",
    "it",
    "its",
    "itself",
    "just",
    "kinda",
    "kids",
    "kid",
    "kind",
    "know",
    "keep",
    "like",
    "me",
    "more",
    "most",
    "my",
    "myself",
    "name",
    "no",
    "nor",
    "not",
    "now",
    "of",
    "off",
    "on",
    "once",
    "one",
    "only",
    "or",
    "other",
    "our",
    "ours",
    "ourselves",
    "out",
    "over",
    "own",
    "same",
    "she",
    "should",
    "so",
    "some",
    "sort",
    "such",
    "than",
    "that",
    "the",
    "their",
    "theirs",
    "them",
    "themselves",
    "then",
    "there",
    "these",
    "they",
    "they're",
    "this",
    "those",
    "through",
    "to",
    "too",
    "under",
    "understand",
    "until",
    "up",
    "very",
    "was",
    "we",
    "we're",
    "we've",
    "we'll",
    "were",
    "what",
    "what's",
    "when",
    "where",
    "which",
    "while",
    "who",
    "whom",
    "why",
    "will",
    "with",
    "you",
    "you've",
    "your",
    "yours",
    "yourself",
    "yourselves",
    "yeah",
    "yes",
    "think",
    "really",
    "maybe",
    "probably",
    "basically",
    "actually",
    "obviously",
    "get",
    "got",
    "go",
    "going",
    "gonna",
    "wanna",
    "want",
    "wants",
    "that's",
    "it's",
    "don't",
    "you're",
    "would",
    "true",
    "okay",
    "well",
    "guess",
    "make",
    "makes",
    "much",
    "question",
    "questions",
    "right",
    "sense",
    "sorry",
    "something",
    "specified",
    "suppose",
    "sure",
    "thing",
    "things",
    "another",
    "anything",
    "et",
    "everyone",
    "farsides",
    "kindness",
    "laugh",
    "laughs",
    "mhm",
    "mm",
    "hmm",
    "pause",
    "umm",
    "um",
    "uh",
    "interviewee",
    "interviewer",
    "interview",
    "interviews",
    "researcher",
    "moderator",
    "facilitator",
    "participant",
    "soi",
    "andi",
    "andthenit",
    "andthen",
}

KOREAN_STOPWORDS = {
    "그리고",
    "그래서",
    "하지만",
    "그러나",
    "저는",
    "제가",
    "우리",
    "때문에",
    "정말",
    "그냥",
    "많이",
    "조금",
    "있는",
    "없는",
    "했습니다",
    "있었습니다",
    "되었습니다",
}

STOPWORDS.update(KOREAN_STOPWORDS)

GENERIC_THEME_TERMS = {
    "ask",
    "asked",
    "basis",
    "bit",
    "call",
    "couple",
    "else",
    "finding",
    "gave",
    "give",
    "good",
    "great",
    "help",
    "helped",
    "little",
    "lot",
    "made",
    "mean",
    "means",
    "need",
    "needed",
    "needs",
    "people",
    "regular",
    "say",
    "said",
    "share",
    "stuff",
    "talk",
    "tell",
    "try",
    "trying",
    "useful",
    "ago",
    "almost",
    "always",
    "area",
    "areas",
    "bare",
    "bad",
    "big",
    "couldn't",
    "couldnt",
    "day",
    "week",
    "weeks",
    "deal",
    "done",
    "else",
    "else's",
    "even",
    "have",
    "minimum",
    "often",
    "possibly",
    "someone",
    "sometimes",
    "specific",
    "task",
    "times",
    "though",
    "ve",
    "way",
    "work",
}

STOPWORDS.update(GENERIC_THEME_TERMS)

THEME_HINTS = {
    "admin": "Administrative Support and Scheduling",
    "administration": "Administrative Support and Scheduling",
    "administrative": "Administrative Support and Scheduling",
    "alignment": "Curriculum Alignment and Assessment",
    "assessment": "Curriculum Alignment and Assessment",
    "peer": "Peer Collaboration",
    "peers": "Peer Collaboration",
    "classmate": "Peer Collaboration",
    "classmates": "Peer Collaboration",
    "community": "Belonging and Course Community",
    "group": "Peer Collaboration",
    "groups": "Peer Collaboration",
    "teacher": "Instructor Support",
    "teachers": "Instructor Support",
    "instructor": "Instructor Support",
    "instructors": "Instructor Support",
    "feedback": "Instructor Support",
    "support": "Instructor Support",
    "supported": "Instructor Support",
    "curriculum": "Curriculum Development",
    "lesson": "Lesson Design",
    "lessons": "Lesson Design",
    "planning": "Planning and Implementation",
    "standards": "Curriculum Alignment and Assessment",
    "training": "Training and Capacity Building",
    "train": "Training and Capacity Building",
    "confidence": "Confidence and Self-Efficacy",
    "confident": "Confidence and Self-Efficacy",
    "isolated": "Isolation and Disconnection",
    "isolation": "Isolation and Disconnection",
    "deadline": "Course Structure and Clarity",
    "deadlines": "Course Structure and Clarity",
    "scheduling": "Administrative Support and Scheduling",
    "platform": "Technology and Access Barriers",
    "online": "Online Learning Experience",
    "autonomy": "Learner Autonomy",
    "independent": "Learner Autonomy",
    "motivation": "Motivation and Persistence",
}

STRONG_LABEL_HINTS = {
    "admin",
    "administration",
    "administrative",
    "instructor",
    "instructors",
    "peer",
    "peers",
    "scheduling",
    "support",
    "teacher",
    "teachers",
    "train",
    "training",
}

THEME_COLORS = (
    "#2563eb",
    "#dc2626",
    "#16a34a",
    "#9333ea",
    "#ea580c",
    "#0891b2",
    "#be123c",
    "#4f46e5",
    "#65a30d",
    "#c2410c",
)

TOKEN_RE = re.compile("[A-Za-z\uac00-\ud7a3][A-Za-z0-9\uac00-\ud7a3'-]*")
TOKEN_NOISE_RE = re.compile(r"p\d+", re.IGNORECASE)
SPEAKER_RE = re.compile("^\\s*([A-Za-z\uac00-\ud7a3][A-Za-z0-9\uac00-\ud7a3 _.-]{0,60})\\s*:\\s+(.+?)\\s*$")
SPEAKER_TIMESTAMP_RE = re.compile(
    "^\\s*([A-Za-z\uac00-\ud7a3][A-Za-z0-9\uac00-\ud7a3 _.'-]{0,60}?)\\s+"
    r"(?:(?:\d+:)?\d{1,2}:\d{2}(?:\.\d+)?)\s*$"
)
INLINE_SPEAKER_TIMESTAMP_RE = re.compile(
    r"(?P<prefix>^|[.!?]\s+|\n)"
    "(?P<header>[A-Za-z\uac00-\ud7a3][A-Za-z0-9\uac00-\ud7a3 _.'-]{0,60}?\\s+"
    r"(?:(?:\d+:)?\d{1,2}:\d{2}(?:\.\d+)?))\s+",
    re.MULTILINE,
)
EMBEDDED_SPEAKER_LABEL_RE = re.compile(
    "(?<=[.!?])\\s+(?=[A-Za-z\uac00-\ud7a3][A-Za-z0-9\uac00-\ud7a3 _.-]{0,60}\\s*:\\s+)"
)
SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")
TIMESTAMP_RE = re.compile(r"^(?:\d+:)?\d{1,2}:\d{2}(?:\.\d+)?$")
TIMESTAMP_RANGE_RE = re.compile(
    r"^(?:\d+:)?\d{1,2}:\d{2}(?:\.\d+)?\s*[-â€“]\s*"
    r"(?:\d+:)?\d{1,2}:\d{2}(?:\.\d+)?$"
)
ROW_NUMBER_RE = re.compile(r"^\d{1,4}$")
OTTER_BOILERPLATE_RE = re.compile(r"transcribed\s+by\s+https?://otter\.ai", re.IGNORECASE)
URL_RE = re.compile(r"https?://", re.IGNORECASE)
PDF_HEADER_RE = re.compile(
    r"^(?:farsides\s+et\s+al\.?\s+\(\d{4}\)\.?\s+autism\s+and\s+kindness|"
    r"transcript\s+of\s+interview\s+\d+\b.*)$",
    re.IGNORECASE,
)
BRACKETED_PAUSE_RE = re.compile(r"\[\s*\d+\s*-\s*second\s+pause\s*\]", re.IGNORECASE)
BRACKETED_METADATA_RE = re.compile(
    r"\[(?:[^\]]*\b(?:specified|named|redacted|anonymi[sz]ed)[^\]]*)\]",
    re.IGNORECASE,
)
BACKCHANNEL_RE = re.compile(
    r"^(?:ok|okay|yeah|yes|no|thank you|thanks|sure|that'?s fine|that'?s good|"
    r"that'?s okay|no that'?s okay|makes sense|hmm|mm hm|mmhm|uh huh)$",
    re.IGNORECASE,
)
EXCLUDED_SPEAKER_RE = re.compile(
    r"\b(drm|interviewer|moderator|facilitator|researcher|host|note\s*taker|notetaker)\b",
    re.IGNORECASE,
)
INTERVIEW_PROCEDURE_RE = re.compile(
    r"\b("
    r"thank\s+you\s+for\s+(joining|participating)"
    r"|goal\s+of\s+this\s+interview"
    r"|do\s+not\s+need\s+to\s+answer"
    r"|will\s+be\s+recorded"
    r"|recorded\s+for\s+research"
    r"|research\s+purpose"
    r"|explain\s+the\s+procedure"
    r"|before\s+we\s+start"
    r"|right\s+or\s+wrong\s+answers"
    r"|stop\s+at\s+any\s+time"
    r"|withdraw\s+at\s+any\s+time"
    r"|your\s+participation\s+is\s+voluntary"
    r"|informed\s+consent"
    r"|consent\s+form"
    r"|confidentiality"
    r"|confidential"
    r"|audio\s+record"
    r"|video\s+record"
    r")\b",
    re.IGNORECASE,
)
INTERVIEW_PROMPT_RE = re.compile(
    r"\b("
    r"can\s+you\s+(tell|describe|share|explain)"
    r"|can\s+you\s+(talk|say)"
    r"|could\s+you\s+(tell|describe|share|explain)"
    r"|would\s+you\s+(tell|describe|share|explain)"
    r"|talk\s+a\s+little\s+bit\s+more"
    r"|tell\s+me\s+about"
    r"|what\s+else"
    r"|what\s+(kinds|kind|types|type|was|were|is|are|did|do|does|makes|made)"
    r"|how\s+(did|do|does|was|were|is|are|has|have)"
    r"|why\s+(did|do|does|was|were|is|are)"
    r")\b",
    re.IGNORECASE,
)
PARTICIPANT_SPEAKER_RE = re.compile(
    r"\b(participant|student|learner|teacher|parent|caregiver|youth|member|p\d+|s\d+|t\d+)\b",
    re.IGNORECASE,
)
GENERIC_IMPORT_SPEAKER_RE = re.compile(
    r"^(unknown(?:\s+speaker)?|transcript|document|page\s+\d+|page\d+)$",
    re.IGNORECASE,
)
NAMED_INTERVIEW_PROMPT_RE = re.compile(
    r"^[A-Z][A-Za-z'-]+(?:\s+[A-Z][A-Za-z'-]+){1,3}\s+"
    r"(can|could|would|what|how|why|tell|talk)\b",
)


