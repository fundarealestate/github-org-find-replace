import os
import re

import github
import click


class Updater:
    def __init__(self, repo, paths):
        self.repo = repo
        self.paths = paths
        self.old_contents = {}
        self.old_shas = {}  # store the shas for the original files, (for use when doing update_file)
        self.new_contents = {}

    def get_old_contents(self):
        for p in self.paths:
            # get the current content from master
            f = self.repo.get_file_contents(p)
            self.old_contents[p] = f.decoded_content.decode("utf-8")
            self.old_shas[p] = f.sha

    def find_replace(self, match_string, replace, regex=False):
        for p, old_content in self.old_contents.items():
            if regex:
                new_content = re.sub(match_string, replace, old_content)
            else:
                new_content = old_content.replace(match_string, replace)

            if new_content == old_content:
                print("(no change in content)")
                continue
            self.new_contents[p] = new_content

            print(f"Old contents of {self.repo} {p}\n---------")
            print(old_content)
            print("---------")
            print(f"New contents of {self.repo} {p}\n---------")
            print(new_content)
            print("---------")

    def create_pr(self, message, branch_name, labels, reviewers):
        if not self.new_contents:
            print("no new contents to use")
            return

        ref = self.repo.create_git_ref(
            f"refs/heads/{branch_name}",
            self.repo.get_commits()[0].sha,
        )

        for p, new_content in self.new_contents.items():
            old_file_sha = self.repo.get_file_contents(p, ref.ref).sha
            self.repo.update_file(
                p,
                message,
                new_content,
                old_file_sha,
                branch_name,
            )

        pull = self.repo.create_pull(
            message,
            "",
            self.repo.default_branch,
            branch_name,
        )

        if labels:
            pull.add_to_labels(*labels)

        if reviewers:
            pull.create_review_request(reviewers)

        print(pull.html_url)


@click.command()
@click.option('-h', '--ghe-hostname', type=str, default="", help='Github Enterprise Hostname, example: \'github.company.com\'')
@click.option('-o', '--organization', type=str, required=True)
@click.option('-f', '--find', type=str, required=True, help='The string that is going to be searched in GH.')
@click.option('-s', '--match-string', type=str, required=False, help='Optional string or regex pattern that is going to be replaced inside the matching files instead of --find')
@click.option('-r', '--replace', type=str, required=True, help='The string or regex pattern to substitute the matched pattern. (regex can be used to put back capture groups i.e. \\1, \\2).')
@click.option('-x', '--regex', required=False, is_flag=True, default=False, help='Activate regex mode over match-string/replace pair.')
@click.option('-e', '--extra-search-params', type=str, default="")
@click.option('-i', '--ignore-existing-branch', is_flag=True, default=False)
@click.option('--commit-msg', type=str, default="", help='Set the commit/PR message ahead of prompt. Can still be overridden by the prompt.')
@click.option('--branch', type=str, default="", help='Set the branch name ahead of prompt. Can still be overridden by the prompt.')
@click.option('--labels', type=str, default="", help='Set the PR labels ahead of prompt. Can still be overridden by the prompt.')
@click.option('--reviewers', type=str, default="", help='Set the PR reviewers ahead of prompt. Can still be overridden by the prompt.')
@click.pass_context
def cli(
    ctx,
    ghe_hostname,
    organization,
    find,
    match_string,
    replace,
    regex,
    extra_search_params,
    commit_msg,
    branch,
    labels,
    reviewers,
    ignore_existing_branch
):

    # allow \n to be given on the command line user input
    # find = find.replace('\\n', '\n')
    # replace = replace.replace('\\n', '\n')

    if not ghe_hostname:
        gh = github.Github(os.environ["GITHUB_API_TOKEN"])
    else:
        gh = github.Github(base_url=f"https://{ghe_hostname}/api/v3", login_or_token=os.environ["GITHUB_API_TOKEN"])

    if not match_string:
        match_string = find

    query = f"org:{organization} {extra_search_params} in:file '{find}'"
    results = gh.search_code(query)

    if not results.totalCount:
        click.echo(f"No results found for:\n{query}")
        return

    repo_objs_by_name = {result.repository.full_name: result.repository for result in results if not result.repository.archived}

    repo_files = {}
    for result in results:
        if result.repository.archived:
            print(f"skipping archived repo {result.repository}")
            continue

        files_in_repo = repo_files.get(result.repository.full_name, [])
        files_in_repo.append(result.path)
        repo_files[result.repository.full_name] = files_in_repo

    updaters = [Updater(repo, repo_files[name]) for name, repo in repo_objs_by_name.items()]

    click.secho(f"Found matches in {len(repo_files)} repos.")
    if not click.confirm("See potential changes?"):
        return

    for u in updaters:
        click.secho(str(u.repo), fg="magenta")
        u.get_old_contents()
        u.find_replace(match_string, replace, regex)

    if not click.confirm("Ready to send these as PRs? We'll get some more information first."):
        return

    title = click.prompt("Commit message / PR title", default=commit_msg)
    branch_name = click.prompt("Branch name", default=branch)
    labels_in_commas = click.prompt("PR labels, comma separated", default=labels)
    labels_list = []
    reviewers_in_commas = click.prompt("PR reviewers (not teams), comma separated", default=reviewers)
    reviewers_list = []

    if labels_in_commas:
        labels_list = [x.strip() for x in labels_in_commas.split(",")]
    if reviewers_in_commas:
        reviewers_list = [x.strip() for x in reviewers_in_commas.split(",")]

    for u in updaters:
        click.secho(str(u.repo), fg="magenta")
        try:
            u.create_pr(title, branch_name, labels_list, reviewers_list)
        except github.GithubException as err:
            if ignore_existing_branch and err.status == 422 and err.data['message'] == 'Reference already exists':
                print(f"Branch already exists on {u.repo}, ignoring it")
            else:
                print("An error has ocurred. Some pull requests may have still been created.")
                raise err


if __name__ == "__main__":
    cli()
