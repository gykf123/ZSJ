#import "AFNetworking.h"
#import "LauncherNavigationController.h"
#import "ModpackInstallViewController.h"
#import "MinecraftResourceDownloadTask.h"
#import "UIKit+AFNetworking.h"
#import "UIKit+hook.h"
#import "WFWorkflowProgressView.h"
#import "modpack/ModrinthAPI.h"
#import "config.h"
#import "ios_uikit_bridge.h"
#import "utils.h"
#include <dlfcn.h>
#include <stdlib.h>

#define kCurseForgeGameIDMinecraft 432
#define kCurseForgeClassIDModpack 4471
#define kCurseForgeClassIDMod 6

@interface ModpackInstallViewController()<UIContextMenuInteractionDelegate>
@property(nonatomic) UISearchController *searchController;
@property(nonatomic) UIMenu *currentMenu;
@property(nonatomic) NSMutableArray *list;
@property(nonatomic) NSMutableDictionary *filters;
@property ModrinthAPI *modrinth;
@property(nonatomic) NSString *projectType;
@end

@implementation ModpackInstallViewController

- (void)viewDidLoad {
    [super viewDidLoad];

    //NSString *curseforgeAPIKey = CONFIG_CURSEFORGE_API_KEY;
    self.searchController = [[UISearchController alloc] initWithSearchResultsController:nil];
    self.searchController.searchResultsUpdater = self;
    self.searchController.obscuresBackgroundDuringPresentation = NO;
    self.navigationItem.searchController = self.searchController;
    self.modrinth = [ModrinthAPI new];
    self.projectType = @"mod";
    self.filters = @{
        @"projectType": @"mod",
        @"name": @" "
        // mcVersion
    }.mutableCopy;

    // 分类切换：Mod / 资源包 / 光影 / 整合包
    UISegmentedControl *seg = [[UISegmentedControl alloc] initWithItems:@[
        localize(@"launcher.resource.mod", nil),
        localize(@"launcher.resource.resourcepack", nil),
        localize(@"launcher.resource.shader", nil),
        localize(@"launcher.resource.modpack", nil)
    ]];
    seg.selectedSegmentIndex = 0;
    seg.autoresizingMask = UIViewAutoresizingFlexibleWidth;
    [seg addTarget:self action:@selector(categoryChanged:) forControlEvents:UIControlEventValueChanged];
    self.navigationItem.titleView = seg;

    [self updateSearchResults];
}

- (void)categoryChanged:(UISegmentedControl *)sender {
    NSArray *types = @[@"mod", @"resourcepack", @"shader", @"modpack"];
    self.projectType = types[sender.selectedSegmentIndex];
    [self.filters removeObjectForKey:@"isModpack"];
    self.filters[@"projectType"] = self.projectType;
    [self updateSearchResults];
}

- (void)loadSearchResultsWithPrevList:(BOOL)prevList {
    NSString *name = self.searchController.searchBar.text;
    if (!prevList && [self.filters[@"name"] isEqualToString:name]) {
        return;
    }

    [self switchToLoadingState];
    dispatch_async(dispatch_get_global_queue(DISPATCH_QUEUE_PRIORITY_DEFAULT, 0), ^{
        self.filters[@"name"] = name;
        self.list = [self.modrinth searchModWithFilters:self.filters previousPageResult:prevList ? self.list : nil];
        dispatch_async(dispatch_get_main_queue(), ^{
            if (self.list) {
                [self switchToReadyState];
                [self.tableView reloadData];
            } else {
                showDialog(localize(@"Error", nil), self.modrinth.lastError.localizedDescription);
                [self actionClose];
            }
        });
    });
}

- (void)updateSearchResults {
    [self loadSearchResultsWithPrevList:NO];
}

- (void)updateSearchResultsForSearchController:(UISearchController *)searchController {
    [NSObject cancelPreviousPerformRequestsWithTarget:self selector:@selector(updateSearchResults) object:nil];
    [self performSelector:@selector(updateSearchResults) withObject:nil afterDelay:0.5];
}

- (void)actionClose {
    [self.navigationController dismissViewControllerAnimated:YES completion:nil];
}

- (void)switchToLoadingState {
    UIActivityIndicatorView *indicator = [[UIActivityIndicatorView alloc] initWithActivityIndicatorStyle:UIActivityIndicatorViewStyleMedium];
    self.navigationItem.rightBarButtonItem = [[UIBarButtonItem alloc] initWithCustomView:indicator];
    [indicator startAnimating];
    self.navigationController.modalInPresentation = YES;
    self.tableView.allowsSelection = NO;
}

- (void)switchToReadyState {
    UIActivityIndicatorView *indicator = (id)self.navigationItem.rightBarButtonItem.customView;
    [indicator stopAnimating];
    self.navigationItem.rightBarButtonItem = [[UIBarButtonItem alloc] initWithBarButtonSystemItem:UIBarButtonSystemItemClose target:self action:@selector(actionClose)];
    self.navigationController.modalInPresentation = NO;
    self.tableView.allowsSelection = YES;
}

#pragma mark UIContextMenu

- (UIContextMenuConfiguration *)contextMenuInteraction:(UIContextMenuInteraction *)interaction configurationForMenuAtLocation:(CGPoint)location
{
    return [UIContextMenuConfiguration configurationWithIdentifier:nil previewProvider:nil actionProvider:^UIMenu * _Nullable(NSArray<UIMenuElement *> * _Nonnull suggestedActions) {
        return self.currentMenu;
    }];
}

- (_UIContextMenuStyle *)_contextMenuInteraction:(UIContextMenuInteraction *)interaction styleForMenuWithConfiguration:(UIContextMenuConfiguration *)configuration
{
    _UIContextMenuStyle *style = [_UIContextMenuStyle defaultStyle];
    style.preferredLayout = 3; // _UIContextMenuLayoutCompactMenu
    return style;
}

#pragma mark UITableViewDataSource

- (NSInteger)numberOfSectionsInTableView:(UITableView *)tableView {
    return 1;
}

- (NSInteger)tableView:(UITableView *)tableView numberOfRowsInSection:(NSInteger)section {
    return self.list.count;
}

- (UITableViewCell *)tableView:(UITableView *)tableView cellForRowAtIndexPath:(NSIndexPath *)indexPath {
    UITableViewCell *cell = [tableView dequeueReusableCellWithIdentifier:@"cell"];
    if (!cell) {
        cell = [[UITableViewCell alloc] initWithStyle:UITableViewCellStyleSubtitle reuseIdentifier:@"cell"];
        cell.imageView.contentMode = UIViewContentModeScaleToFill;
        cell.imageView.clipsToBounds = YES;
    }

    NSDictionary *item = self.list[indexPath.row];
    cell.textLabel.text = item[@"title"];
    cell.detailTextLabel.text = item[@"description"];
    UIImage *fallbackImage = [UIImage imageNamed:@"DefaultProfile"];
    [cell.imageView setImageWithURL:[NSURL URLWithString:item[@"imageUrl"]] placeholderImage:fallbackImage];

    if (!self.modrinth.reachedLastPage && indexPath.row == self.list.count-1) {
        [self loadSearchResultsWithPrevList:YES];
    }

    return cell;
}

- (void)showDetails:(NSDictionary *)details atIndexPath:(NSIndexPath *)indexPath {
    UITableViewCell *cell = [self.tableView cellForRowAtIndexPath:indexPath];

    NSMutableArray<UIAction *> *menuItems = [[NSMutableArray alloc] init];
    [details[@"versionNames"] enumerateObjectsUsingBlock:
    ^(NSString *name, NSUInteger i, BOOL *stop) {
        NSString *nameWithVersion = name;
        NSString *mcVersion = details[@"mcVersionNames"][i];
        if (![name hasSuffix:mcVersion]) {
            nameWithVersion = [NSString stringWithFormat:@"%@ - %@", name, mcVersion];
        }
        [menuItems addObject:[UIAction
            actionWithTitle:nameWithVersion
            image:nil identifier:nil
            handler:^(UIAction *action) {
            [self actionClose];
            NSString *tmpIconPath = [NSTemporaryDirectory() stringByAppendingPathComponent:@"icon.png"];
                [UIImagePNGRepresentation([cell.imageView.image _imageWithSize:CGSizeMake(40, 40)]) writeToFile:tmpIconPath atomically:YES];
            if ([self.projectType isEqualToString:@"modpack"]) {
                [self.modrinth installModpackFromDetail:self.list[indexPath.row] atIndex:i];
            } else {
                [self installResourceFromDetail:self.list[indexPath.row] atIndex:i projectType:self.projectType];
            }
        }]];
    }];

    self.currentMenu = [UIMenu menuWithTitle:@"" children:menuItems];
    UIContextMenuInteraction *interaction = [[UIContextMenuInteraction alloc] initWithDelegate:self];
    cell.detailTextLabel.interactions = @[interaction];
    [interaction _presentMenuAtLocation:CGPointZero];
}

- (void)tableView:(UITableView *)tableView didSelectRowAtIndexPath:(NSIndexPath *)indexPath {
    NSDictionary *item = self.list[indexPath.row];
    if ([item[@"versionDetailsLoaded"] boolValue]) {
        [self showDetails:item atIndexPath:indexPath];
        return;
    }
    [tableView deselectRowAtIndexPath:indexPath animated:NO];
    [self switchToLoadingState];
dispatch_async(dispatch_get_global_queue(DISPATCH_QUEUE_PRIORITY_DEFAULT, 0), ^{
        [self.modrinth loadDetailsOfMod:self.list[indexPath.row]];
        dispatch_async(dispatch_get_main_queue(), ^{
            [self switchToReadyState];
            if ([item[@"versionDetailsLoaded"] boolValue]) {
                [self showDetails:item atIndexPath:indexPath];
            } else {
                showDialog(localize(@"Error", nil), self.modrinth.lastError.localizedDescription);
            }
        });
    });
}

- (void)installResourceFromDetail:(NSDictionary *)detail atIndex:(NSUInteger)index projectType:(NSString *)projectType {
    NSString *url = detail[@"versionUrls"][index];
    if (!url) {
        showDialog(localize(@"Error", nil), localize(@"launcher.resource.no_url", nil));
        return;
    }
    NSUInteger size = [detail[@"versionSizes"][index] unsignedLongLongValue];
    id shaObj = detail[@"versionHashes"][index];
    NSString *sha = [shaObj isKindOfClass:NSString.class] ? shaObj : nil;
    NSString *subdir = [projectType isEqualToString:@"resourcepack"] ? @"resourcepacks"
                        : ([projectType isEqualToString:@"shader"] ? @"shaderpacks" : @"mods");
    NSString *baseDir = @(getenv("POJAV_GAME_DIR"));
    if (baseDir.length == 0) baseDir = NSHomeDirectory();
    NSString *destDir = [baseDir stringByAppendingPathComponent:subdir];
    NSString *filename = [url lastPathComponent];
    NSString *destPath = [destDir stringByAppendingPathComponent:filename];
    [NSFileManager.defaultManager createDirectoryAtPath:destDir withIntermediateDirectories:YES attributes:nil error:nil];

    MinecraftResourceDownloadTask *task = [MinecraftResourceDownloadTask new];
    task.handleError = ^{
        dispatch_async(dispatch_get_main_queue(), ^{
            [self switchToReadyState];
        });
    };
    [task prepareForDownload];
    NSURLSessionDownloadTask *dl = [task createDownloadTask:url size:size sha:sha altName:detail[@"title"] toPath:destPath success:^{
        dispatch_async(dispatch_get_main_queue(), ^{
            [self switchToReadyState];
            showDialog(localize(@"launcher.resource.done.title", nil),
                [NSString stringWithFormat:localize(@"launcher.resource.done.message", nil), subdir, destPath]);
        });
    }];
    if (dl) {
        [dl resume];
    } else {
        [self switchToReadyState];
    }
}

@end
