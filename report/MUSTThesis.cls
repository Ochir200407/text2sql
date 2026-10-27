%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
% MUST - Дипломын ажлын LaTeX класс файл
% Файлын нэр: MUST-Diploma.cls
% Хувилбар: 2.0 (2026/02/27)
%
% Тайлбар:
%   Энэ класс файл нь ШУТИС-ийн оюутны дипломын ажлын
%   загвар файлын (main.tex) бүтэц, хэлбэрийг тодорхойлно.
%
% Эх сурвалж:
%   http://www.LaTeXTemplates.com загвар дээр суурилсан.
%   Анхны зохиогч: Vel (vel@latextemplates.com), Johannes Böttcher
%
% Лиценз:
%   LPPL v1.3c (http://www.latex-project.org/lppl)
%
%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

%===============================================================================
%  1. КЛАССЫН ТОДОРХОЙЛОЛТ БА ПАРАМЕТРҮҮД
%===============================================================================

\NeedsTeXFormat{LaTeX2e}[1996/12/01]
\newcommand{\classname}{MUST-Diploma}
\ProvidesClass{\classname}[2026/02/27 v2.0 MUST Diploma Thesis Class]
\providecommand{\baseclass}{book}

% --- Шаардлагатай үндсэн багцууд ---
\RequirePackage{etoolbox}
\RequirePackage{xparse}

% --- Boolean хувьсагчид ---
\newbool{nolistspace}
\newbool{chapteroneline}
\newbool{listtoc}
\newbool{toctoc}
\newbool{parskip}
\newbool{hyperrefsupport}
\booltrue{hyperrefsupport}
\newbool{headsepline}

% --- Классын сонголтууд ---
\DeclareOption{nohyperref}{\boolfalse{hyperrefsupport}}
\DeclareOption{nolistspacing}{\booltrue{nolistspace}}
\DeclareOption{liststotoc}{\booltrue{listtoc}}
\DeclareOption{chapterinoneline}{\booltrue{chapteroneline}}
\DeclareOption{toctotoc}{\booltrue{toctoc}}
\DeclareOption{parskip}{\booltrue{parskip}}
\DeclareOption{headsepline}{\booltrue{headsepline}}
\DeclareOption*{\PassOptionsToClass{\CurrentOption}{\baseclass}}

\ProcessOptions\relax
\LoadClass{\baseclass}

%===============================================================================
%  2. КЛАССЫН СОНГОЛТУУДЫН ТОХИРГОО
%===============================================================================

% --- Параграф хоорондын зай ---
\ifbool{parskip}{\RequirePackage{parskip}}{}

% --- Бүлгийн толгой хэсгийн тохиргоо ---
\newcommand{\abovechapterspace}{\vspace*{50pt}}
\patchcmd{\@makechapterhead}{\vspace*{50\p@}}{\abovechapterspace}{}{}
\patchcmd{\@makeschapterhead}{\vspace*{50\p@}}{\abovechapterspace}{}{}
\newcommand{\autodot}{}
\newcommand{\mdtChapapp}{}

\ifbool{chapteroneline}{%
	\patchcmd{\@makechapterhead}{%
		\pagecolor{yellow}
		\if@mainmatter
		\huge\bfseries \@chapapp\space \thechapter
		\par\nobreak
		\vskip 20\p@
		\fi
	}{%
		\if@mainmatter
		\Huge\bfseries \mdtChapapp\thechapter\autodot\enspace
		\fi
	}{}{}
}

% --- Жагсаалтуудыг гарчигт нэмэх сонголт ---
\ifbool{listtoc}{%
	\patchcmd{\listoftables}{\@starttoc{lot}}{%
		\addchaptertocentry{\listtablename}\@starttoc{lot}}{}{}%
	\patchcmd{\listoffigures}{\@starttoc{lof}}{%
		\addchaptertocentry{\listfigurename}\@starttoc{lof}}{}{}%
}

% --- Гарчгийн хүснэгтийг гарчигт нэмэх сонголт ---
\ifbool{toctoc}{%
	\patchcmd{\tableofcontents}{\@starttoc{toc}}{%
		\addchaptertocentry{\contentsname}\@starttoc{toc}}{}{}%
}

% --- Гарчгийн том үсгийн тохиргоо ---
\patchcmd{\tableofcontents}{\MakeUppercase}{\MakeMarkcase}{}{}
\patchcmd{\tableofcontents}{\MakeUppercase}{\MakeMarkcase}{}{}
\patchcmd{\listoffigures}{\MakeUppercase}{\MakeMarkcase}{}{}
\patchcmd{\listoffigures}{\MakeUppercase}{\MakeMarkcase}{}{}
\patchcmd{\listoftables}{\MakeUppercase}{\MakeMarkcase}{}{}
\patchcmd{\listoftables}{\MakeUppercase}{\MakeMarkcase}{}{}

% --- Жагсаалтын мөр хоорондын зайг single болгох сонголт ---
%     (onehalfspacing, doublespacing горимд хэрэгтэй)
\ifbool{nolistspace}{
	\patchcmd{\listoffigures}{%
		\@starttoc{lof}}{%
			\begingroup%
			\singlespace\@starttoc{lof}\endgroup%
		}{}{}%
	\patchcmd{\listoftables}{%
		\@starttoc{lot}}{%
			\begingroup%
			\singlespace\@starttoc{lot}\endgroup%
		}{}{}%
	\patchcmd{\tableofcontents}{%
		\@starttoc{toc}}{%
			\begingroup%
			\singlespace\@starttoc{toc}\endgroup%
		}{}{}%
}{}

%===============================================================================
%  3. ДУГААРГҮЙ БҮЛЭГ/ХЭСЭГ НЭМЭХ КОМАНДУУД
%===============================================================================

% Дугааргүй бүлгийг гарчигт оруулж, толгой мөрийг тохируулна
\ProvideDocumentCommand{\addchap}{ s o m }{%
	\chapter*{#3}%
	\markboth{}{}%
	\IfBooleanTF{#1}{%
	}{%
		\IfNoValueTF{#2}{%
			\addchaptertocentry{#3}%
			\markboth{\MakeMarkcase{#3}}{\MakeMarkcase{#3}}%
		}{%
			\addchaptertocentry{#2}%
			\markboth{\MakeMarkcase{#2}}{\MakeMarkcase{#2}}%
		}%
	}%
}%

% Дугааргүй хэсгийг гарчигт оруулж, толгой мөрийг тохируулна
\ProvideDocumentCommand{\addsec}{ s o m }{%
	\section*{#3}%
	\markright{}%
	\IfBooleanTF{#1}{%
	}{%
		\IfNoValueTF{#2}{%
			\addcontentsline{toc}{section}{#3}%
			\markright{\MakeMarkcase{#3}}%%
		}{%
			\addcontentsline{toc}{section}{#2}%
			\markright{\MakeMarkcase{#2}}%
		}%
	}%
}%

%===============================================================================
%  4. ШААРДЛАГАТАЙ БАГЦУУД
%===============================================================================

\RequirePackage{setspace}    % Мөр хоорондын зайг өөрчлөх
\RequirePackage{longtable}   % Хуудас дамнасан хүснэгт (товчилсон үгс, тогтмолд ашиглагдана)
\RequirePackage{siunitx}     % SI нэгжийн \SI команд
\RequirePackage{graphicx}    % Зураг оруулах
\graphicspath{{Figures/}{./}}
\RequirePackage{booktabs}    % Хүснэгтийн хэлбэржүүлэлт
\RequirePackage{caption}     % Зургийн гарчгийн тохиргоо
\captionsetup{justification=centerlast, font=small, labelfont=sc, margin=50pt}

%===============================================================================
%  5. ТУСЛАХ КОМАНДУУД
%===============================================================================

% Twosided горимд агуулгыг дараачийн сондгой хуудсанд шилжүүлэх
\newcommand{\checktoopen}{%
	\if@openright\cleardoublepage\else\clearpage\fi
	\ifdef{\phantomsection}{\phantomsection}{}%
}

% Лог руу тусгаарлагч шугам бичих
\newcommand\bhrule{\typeout{--------------------}}
\newcommand\tttypeout[1]{\bhrule\typeout{\space #1}\bhrule}

% Нүүр хуудсанд хэвтээ шугам татах
\newcommand{\HRule}{\rule{\linewidth}{0.5mm}}

% Бүлгийн толгойн дээрх сул зайг багасгах
\renewcommand{\abovechapterspace}{\vspace*{10pt}}

% Гарчиг дахь агуулгын гүн (3 = subsubsection хүртэл)
\setcounter{tocdepth}{3}
\providecommand\addchaptertocentry[1]{%
	\addcontentsline{toc}{chapter}{#1}%
}

%===============================================================================
%  6. БҮЛГИЙН ТОЛГОЙН ХЭЛБЭРЖҮҮЛЭЛТ
%===============================================================================

% Гоёмсог хэлбэр (titlesec багц шаардлагатай)
\newcommand\myformat{
	\titleformat{\chapter}[display]
	{%  формат
		\normalfont\Large\filcenter\bfseries
	}
	{%  шошго
		\vspace*{\fill}
		\titlerule[1pt]%
		\vspace{1pt}%
		\titlerule
		\vspace{1pc}%
		\LARGE\MakeUppercase{\chaptertitlename}~\thechapter%
	}
	{1pc}%  зааглагч
	{%  толгойн өмнө
		\titlerule\Huge
	}
	[%  толгойн дараа
		\vspace*{\fill}
		\thispagestyle{empty}
		\clearpage
		\addtocounter{page}{-1}
	]
}

% Анхдагч хэлбэр
\newcommand\defformat{
	\titleformat{\chapter}[display]
	{\normalfont\huge\bfseries}{\chaptertitlename\ \thechapter}{20pt}{\Huge}
}

%===============================================================================
%  7. ХУУДАСНЫ ЗАЙ (MARGINS)
%===============================================================================

\RequirePackage{geometry}

\geometry{
	headheight=4ex,
	paper=a4paper,       % Цаасны хэмжээ
	inner=4.0cm,         % Дотоод зай
	outer=2.5cm,         % Гадаад зай
	bindingoffset=0cm,   % Үдэлтийн зай
	top=2.0cm,           % Дээд зай
	bottom=3.5cm,        % Доод зай
	footskip=1.5cm,
	%showframe,          % Хуудасны хүрээг харуулах (debug)
}
\raggedbottom

%===============================================================================
%  8. ТОРГУУЛЬ (PENALTIES)
%===============================================================================

\doublehyphendemerits=10000            % Дараалсан мөр зураас тавихгүй
\brokenpenalty=10000                   % Баганы/хуудасны хооронд үг тасдахгүй
\widowpenalty=9999                     % Хуудасны доод мөрийн ганцаардал
\clubpenalty=9999                      % Хуудасны дээд мөрийн ганцаардал
\interfootnotelinepenalty=9999         % Зүүлтийг хуудсаар тасдахгүй

%===============================================================================
%  9. ТОЛГОЙ БА ХӨЛ МӨР (HEADERS & FOOTERS)
%===============================================================================

\RequirePackage[markcase=used]{scrlayer-scrpage}

\providepairofpagestyles{thesisSimple}{%
	\clearpairofpagestyles%
	\automark[chapter]{chapter}
	\ihead{\leftmark}%              Дотоод толгой
	%\ohead{\rightmark}%            Гадаад толгой
	\cfoot[\pagemark]{\pagemark}%   Дунд хөл
}
\ifoot{}%  Дотоод хөл
\ofoot{}%  Гадаад хөл

\pagestyle{thesisSimple}

\providepairofpagestyles[thesisSimple]{thesis}{%
	\automark*[section]{}%
}

\providepairofpagestyles[thesisSimple]{review}{%
	\ofoot[\ttitle/\authorname]{\ttitle/\authorname}
	\ifoot[\today]{\today}
}

\pagestyle{thesis}

\ifbool{headsepline}{\KOMAoption{headsepline}{true}}{}

% fancyhdr багцтай зөрчилтэй тул хориглох
\PreventPackageFromLoading[\ClassError{\classname}{Package `fancyhdr' is
incompatible\MessageBreak with this class}{The pagesyles are defined
using package `scrlayer-scrpage', please consult the\MessageBreak
KOMA-script documentation for details.}]{fancyhdr}

% Хоосон хуудасны хэв маяг
\newcommand{\blank@p@gestyle}{empty}
\newcommand{\blankpagestyle}[1]{\def\blank@p@gestyle{#1}}

\def\cleardoublepage{\clearpage\if@twoside \ifodd\c@page\else
\hbox{}
\thispagestyle{\blank@p@gestyle}
\newpage
\if@twocolumn\hbox{}\newpage\fi\fi\fi}

%===============================================================================
%  10. ТЕЗИСИЙН МЭДЭЭЛЛИЙН КОМАНДУУД
%===============================================================================

\def\ttitle{}
\newcommand*{\supervisor}[1]{\def\supname{#1}}          % Удирдагч багш
\newcommand*{\thesistitle}[1]{\def\@title{#1}\def\ttitle{#1}}   % Тезисийн гарчиг (Монгол)
\newcommand*{\thesistitleeng}[1]{\def\@title{#1}\def\ttitleng{#1}} % Тезисийн гарчиг (Англи)
\newcommand*{\thesistype}[1]{\def\thesisname{#1}}       % Тезисийн төрөл
\newcommand*{\reader}[1]{\def\readname{#1}}              % Шүүмжлэгч
\newcommand*{\advisorA}[1]{\def\advicenameA{#1}}         % Зөвлөх A
\newcommand*{\advisorB}[1]{\def\advicenameB{#1}}         % Зөвлөх B
\newcommand*{\degreeind}[1]{\def\degreeid{#1}}           % Зэргийн индекс
\newcommand*{\degree}[1]{\def\degreename{#1}}            % Зэрэг
\newcommand*{\authorcode}[1]{\def\studentcode{#1}}       % Оюутны код
\newcommand*{\authorshort}[1]{\def\shortname{#1}}        % Зохиогчийн товч нэр
\newcommand*{\authorlong}[1]{\def\longname{#1}}          % Зохиогчийн бүтэн нэр
\newcommand*{\addresses}[1]{\def\addressname{#1}}        % Хаяг
\newcommand*{\university}[1]{\def\univname{#1}}          % Их сургууль
\newcommand*{\department}[1]{\def\deptname{#1}}          % Тэнхим
\newcommand*{\deptchair}[1]{\def\chairname{#1}}          % Тэнхимийн эрхлэгч
\newcommand*{\group}[1]{\def\groupname{#1}}              % Бүлэг/Анги
\newcommand*{\faculty}[1]{\def\facname{#1}}              % Сургууль/Факультет
\newcommand*{\subject}[1]{\def\subjectname{#1}}          % Хичээл/Сэдэв
\newcommand*{\keywords}[1]{\def\keywordnames{#1}}        % Түлхүүр үгс
\newcommand*{\phonenumber}[1]{\def\phonenum{#1}}         % Утасны дугаар

%===============================================================================
%  11. ТУСГАЙ ХУУДАСНЫ ЗАГВАРУУД
%===============================================================================

% --- Мэдэгдлийн хуудас (Declaration) ---
\newenvironment{declaration}{
	\checktoopen
	\tttypeout{\authorshipname}
	\thispagestyle{plain}
	\null\vfil
	{\noindent\huge\bfseries\authorshipname\par\vspace{10pt}}
}{}

% --- Зориулалтын хуудас (Dedication) ---
\newcommand\dedicatory[1]{
	\checktoopen
	\tttypeout{Dedicatory}
	\null\vfil
	\thispagestyle{plain}
	\begin{center}{\Large\slshape #1}\end{center}
	\vfil\null
}

% --- Хураангуйн хуудас (Abstract) ---
\newenvironment{abstract}{
	\checktoopen
	\tttypeout{\abstractname}
	\null\vfil
	\thispagestyle{plain}
	\begin{center}
		{\scshape\Large \univname\par}           % Их сургуулийн нэр
		{\scshape\large \facname\par}\vspace{0.5cm} % Факультетийн нэр
		{\huge\textbf{{\abstractname}} \par}
		\bigskip \
		{\normalsize \Large{\@title} \par}       % Тезисийн нэр
		\medskip
		{\normalsize \shortname \par} \\         % Зохиогчийн нэр
		\addressname
	\end{center}
}

% --- Товчилсон үгсийн хуудас (Abbreviations) ---
\NewDocumentEnvironment{abbreviations}{ m }{%
	\ifbool{nolistspace}{\begingroup\singlespacing}{}
	\ifbool{listtoc}{\addchap{\abbrevname}}{\addchap*{\abbrevname}}
	\begin{longtable}{#1}
}{%
	\end{longtable}
	\addtocounter{table}{-1}
	\ifbool{nolistspace}{\endgroup}{}
}

% --- Физик тогтмолын хуудас (Constants) ---
\NewDocumentEnvironment{constants}{ m }{%
	\ifbool{nolistspace}{\begingroup\singlespacing}{}
	\ifbool{listtoc}{\addchap{\constantsname}}{\addchap*{\constantsname}}
	\begin{longtable}{#1}
}{%
	\end{longtable}
	\addtocounter{table}{-1}
	\ifbool{nolistspace}{\endgroup}{}
}

% --- Тэмдэглэгээний хуудас (Symbols) ---
\NewDocumentEnvironment{symbols}{ m }{%
	\ifbool{nolistspace}{\begingroup\singlespacing}{}
	\ifbool{listtoc}{\addchap{\symbolsname}}{\addchap*{\symbolsname}}
	\begin{longtable}{#1}
}{%
	\end{longtable}
	\addtocounter{table}{-1}
	\ifbool{nolistspace}{\endgroup}{}
}

% --- Талархлын хуудас (Acknowledgements) ---
\NewDocumentEnvironment{acknowledgements}{}{%
	\checktoopen
	\tttypeout{\acknowledgementname}
	\thispagestyle{plain}
	\begin{center}{\huge \textbf{{\acknowledgementname}}\par}\end{center}
}
{
	\vfil\vfil\null
}

%===============================================================================
%  12. HYPERREF ТОХИРГОО
%===============================================================================

\ifbool{hyperrefsupport}{%
	\AfterPackage{biblatex}{%
		\RequirePackage{hyperref}
		\hypersetup{
			pdfpagemode={UseOutlines},
			bookmarksopen=true,
			bookmarksopenlevel=0,
			hypertexnames=false,
			colorlinks=true,       % Холбоосыг өнгөтэй харуулах
			citecolor=magenta,     % Эшлэлийн өнгө
			linkcolor=red,         % Дотоод холбоосын өнгө
			urlcolor=black,        % URL холбоосын өнгө
			pdfstartview={FitV},
			unicode,
			breaklinks=true,
		}
		\pdfstringdefDisableCommands{%
			\let\\\space%
		}
	}
}{%
	% hyperref идэвхгүй
}

%===============================================================================

\endinput
